import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.rag.agent.graph import get_agent
from app.rag.agent.graph_streaming import (
    get_streaming_agent,
    get_streaming_agent_with_memory,
    synthesize_streaming,
)
from app.rag.chain.rag_chain import build_chain
from app.rag.memory.store import append_turn, clear_session, get_recent_turns
from app.rag.schemas import (
    QueryRequest,
    QueryResponse,
    Source,
    StreamQueryRequest,
)

MEMORY_WINDOW_SIZE = 6  # last 6 turns = 3 user/assistant pairs

router = APIRouter()

SNIPPET_CHARS = 200


def _sse(payload: dict) -> str:
    """Format a payload as a Server-Sent Events frame.

    The trailing double-newline is required by the SSE protocol; without it
    browsers / clients will not flush the event.
    """
    return f"data: {json.dumps(payload)}\n\n"


def _snippet(text: str) -> str:
    return text[:SNIPPET_CHARS] + ("..." if len(text) > SNIPPET_CHARS else "")


@router.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    chain = build_chain(k=req.k)
    result = chain.invoke(req.question)

    sources = [
        Source(
            title=doc.metadata.get("title", "Unknown"),
            url=doc.metadata.get("url", ""),
            source=doc.metadata.get("source", "unknown"),
            snippet=(
                doc.page_content[:SNIPPET_CHARS]
                + ("..." if len(doc.page_content) > SNIPPET_CHARS else "")
            ),
        )
        for doc in result["docs"]
    ]
    return QueryResponse(
        answer=result["answer"],
        sources=sources,
        retrieved_chunks=len(result["docs"]),
    )


@router.post("/agent_query", response_model=QueryResponse)
def agent_query(req: QueryRequest) -> QueryResponse:
    """Routing RAG agent (LangGraph): classify -> retrieve -> grade -> [rewrite]
    -> synthesize. Same response shape as /query so callers can A/B."""
    agent = get_agent()
    result = agent.invoke({"question": req.question})
    docs = result.get("documents", [])
    sources = [
        Source(
            title=d.metadata.get("title", "Unknown"),
            url=d.metadata.get("url", ""),
            source=d.metadata.get("source", "unknown"),
            snippet=_snippet(d.page_content),
        )
        for d in docs
    ]
    return QueryResponse(
        answer=result.get("answer", ""),
        sources=sources,
        retrieved_chunks=len(docs),
    )


@router.post("/agent_query_stream")
async def agent_query_stream(req: QueryRequest) -> StreamingResponse:
    """Streaming variant of /agent_query.

    Two-phase: (1) run the streaming graph to completion (classify, retrieve,
    grade, optionally rewrite + retry once) and emit a single metadata frame
    with the route, grade, trace, and sources; (2) stream synthesis tokens as
    they arrive from Claude. Closes with a `done` frame.

    Frames are Server-Sent Events: `data: {json}\\n\\n`. Use with curl -N or a
    browser EventSource / fetch+ReadableStream client.

    NOTE: the non-streaming /agent_query is preserved byte-identical so RAGAS,
    the agent probes, and any A/B caller continue to work.
    """
    streaming_agent = get_streaming_agent()

    async def event_stream():
        try:
            state = await streaming_agent.ainvoke({"question": req.question})
        except Exception as e:
            yield _sse({"type": "error", "message": f"agent failed: {e}"})
            yield _sse({"type": "done"})
            return

        docs = state.get("documents", [])
        yield _sse(
            {
                "type": "metadata",
                "route": state.get("route"),
                "grade": state.get("grade"),
                "attempt": state.get("attempt"),
                "trace": state.get("trace", []),
                "sources": [
                    {
                        "title": d.metadata.get("title", "Unknown"),
                        "url": d.metadata.get("url", ""),
                        "source": d.metadata.get("source", "unknown"),
                        "snippet": _snippet(d.page_content),
                    }
                    for d in docs
                ],
                "retrieved_chunks": len(docs),
            }
        )

        async for event in synthesize_streaming(state):
            yield _sse(event)

        yield _sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/agent_query_stream_v2")
async def agent_query_stream_v2(req: StreamQueryRequest) -> StreamingResponse:
    """Multi-turn streaming variant. When session_id is provided, loads the
    last MEMORY_WINDOW_SIZE turns from the SQLite conversation store, runs the
    coref-resolve -> classify -> retrieve -> grade -> [rewrite] graph, then
    streams synthesis with conversation history threaded into the prompt. The
    user's turn is persisted BEFORE the LLM runs (so it survives mid-stream
    errors); the assistant's turn is persisted only after answer_complete fires
    (a half-streamed answer is not safe to save).

    When session_id is None the endpoint degrades to single-turn behaviour
    identical to /agent_query_stream, just routed through the with-memory
    graph (resolve_query short-circuits when history is empty).
    """
    agent = get_streaming_agent_with_memory()
    session_id = req.session_id

    history: list[dict] = []
    if session_id:
        turns = get_recent_turns(session_id, n=MEMORY_WINDOW_SIZE)
        history = [t.to_dict() for t in turns]

    async def event_stream():
        # Persist the user turn FIRST so we keep it on mid-stream failure.
        if session_id:
            append_turn(session_id, "user", req.question)

        try:
            state = await agent.ainvoke(
                {
                    "question": req.question,
                    "history": history,
                    "session_id": session_id,
                }
            )
        except Exception as e:
            yield _sse({"type": "error", "message": f"agent failed: {e}"})
            yield _sse({"type": "done"})
            return

        docs = state.get("documents", [])
        yield _sse(
            {
                "type": "metadata",
                "session_id": session_id,
                "resolved_question": state.get("resolved_question"),
                "route": state.get("route"),
                "grade": state.get("grade"),
                "attempt": state.get("attempt"),
                "trace": state.get("trace", []),
                "sources": [
                    {
                        "title": d.metadata.get("title", "Unknown"),
                        "url": d.metadata.get("url", ""),
                        "source": d.metadata.get("source", "unknown"),
                        "snippet": _snippet(d.page_content),
                    }
                    for d in docs
                ],
                "retrieved_chunks": len(docs),
                "history_turns_loaded": len(history),
            }
        )

        # Stream synthesis; accumulate parts for the assistant-turn persist.
        full_answer_parts: list[str] = []
        async for event in synthesize_streaming(state):
            if event.get("type") == "token":
                full_answer_parts.append(event.get("content", ""))
            yield _sse(event)

        # Persist assistant turn ONLY after a complete answer; a half-streamed
        # answer corrupted by an error frame is not safe to save.
        full_answer = "".join(full_answer_parts)
        if session_id and full_answer:
            append_turn(session_id, "assistant", full_answer)

        yield _sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.delete("/sessions/{session_id}")
def clear_session_endpoint(session_id: str) -> dict:
    """Clear all conversation turns for a session. Returns rows removed."""
    removed = clear_session(session_id)
    return {"session_id": session_id, "turns_removed": removed}


@router.get("/sessions/{session_id}/turns")
def get_session_turns_endpoint(session_id: str, limit: int = 50) -> dict:
    """Return stored turns for a session, oldest-first. Used by the frontend
    to hydrate the chat on page reload. Returns only what was persisted:
    role + content + index + timestamp. Route/grade/sources are NOT stored
    per turn, so hydrated messages render without those badges (correct: we
    don't know after-the-fact)."""
    turns = get_recent_turns(session_id, n=limit)
    return {
        "session_id": session_id,
        "turns": [
            {
                "role": t.role,
                "content": t.content,
                "turn_index": t.turn_index,
                "timestamp": t.timestamp,
            }
            for t in turns
        ],
    }


@router.post("/agent_query_debug")
def agent_query_debug(req: QueryRequest) -> dict:
    """Dev endpoint: agent_query + full agent state (route, grade, trace).
    Same generation; surfaces the classifier and grader decisions for debugging."""
    agent = get_agent()
    result = agent.invoke({"question": req.question})
    return {
        "question": req.question,
        "route": result.get("route"),
        "grade": result.get("grade"),
        "attempt": result.get("attempt"),
        "trace": result.get("trace", []),
        "answer": result.get("answer"),
        "source_titles": [
            d.metadata.get("title") for d in result.get("documents", [])
        ],
    }
