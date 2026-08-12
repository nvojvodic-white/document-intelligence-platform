"""Chat over the caller's own knowledge base.

Every endpoint here takes `user_id: str = CurrentUser` and puts it into the
graph state. Retrieval reads it from there; there is no default and no
fallback, so a bug that dropped it produces an error rather than a silent
search of someone else's documents.

Conversation memory is scoped the same way: session ids are namespaced by
user, so guessing another user's session id does not read their history.
"""
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.deps import CurrentUser
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


def _scoped_session(user_id: str, session_id: str | None) -> str | None:
    """Namespace a session id by user.

    Session ids come from the client, so without this a caller could read
    another user's conversation by guessing theirs. Prefixing with the verified
    user id makes that impossible without touching the memory store's schema.
    """
    return f"{user_id}:{session_id}" if session_id else None


def _sources(docs) -> list[Source]:
    """Citations back to the source file, as this user names it."""
    return [
        Source(
            title=d.metadata.get("title", "unknown"),
            url=d.metadata.get("url", ""),
            source=d.metadata.get("source", "unknown"),
            file_id=d.metadata.get("file_id", ""),
            chunk_id=d.metadata.get("chunk_id", ""),
            snippet=_snippet(d.page_content),
        )
        for d in docs
    ]


@router.post("/query", response_model=QueryResponse)
def query(req: QueryRequest, user_id: str = CurrentUser) -> QueryResponse:
    """Single-shot RAG over this user's documents."""
    chain = build_chain(user_id, k=req.k)
    result = chain.invoke(req.question)
    return QueryResponse(
        answer=result["answer"],
        sources=_sources(result["docs"]),
        retrieved_chunks=len(result["docs"]),
    )


@router.post("/agent_query", response_model=QueryResponse)
def agent_query(req: QueryRequest, user_id: str = CurrentUser) -> QueryResponse:
    """Routing RAG agent (LangGraph): classify -> retrieve -> grade -> [rewrite]
    -> synthesize, over this user's documents."""
    agent = get_agent()
    result = agent.invoke({"question": req.question, "user_id": user_id})
    docs = result.get("documents", [])
    return QueryResponse(
        answer=result.get("answer", ""),
        sources=_sources(docs),
        retrieved_chunks=len(docs),
    )


@router.post("/agent_query_stream")
async def agent_query_stream(
    req: QueryRequest, user_id: str = CurrentUser
) -> StreamingResponse:
    """Streaming variant of /agent_query.

    Two-phase: (1) run the streaming graph to completion (classify, retrieve,
    grade, optionally rewrite + retry once) and emit a single metadata frame
    with the route, grade, trace, and sources; (2) stream synthesis tokens as
    they arrive. Closes with a `done` frame.
    """
    streaming_agent = get_streaming_agent()

    async def event_stream():
        try:
            state = await streaming_agent.ainvoke(
                {"question": req.question, "user_id": user_id}
            )
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
                "sources": [s.model_dump() for s in _sources(docs)],
                "retrieved_chunks": len(docs),
            }
        )

        async for event in synthesize_streaming(state):
            yield _sse(event)

        yield _sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/agent_query_stream_v2")
async def agent_query_stream_v2(
    req: StreamQueryRequest, user_id: str = CurrentUser
) -> StreamingResponse:
    """Multi-turn streaming variant.

    When session_id is provided, loads the last MEMORY_WINDOW_SIZE turns,
    runs coref-resolve -> classify -> retrieve -> grade -> [rewrite], then
    streams synthesis with history threaded into the prompt. The user's turn is
    persisted BEFORE the LLM runs (so it survives mid-stream errors); the
    assistant's turn is persisted only after a complete answer.
    """
    agent = get_streaming_agent_with_memory()
    session_id = _scoped_session(user_id, req.session_id)

    history: list[dict] = []
    if session_id:
        history = [t.to_dict() for t in get_recent_turns(session_id, n=MEMORY_WINDOW_SIZE)]

    async def event_stream():
        # Persist the user turn FIRST so we keep it on mid-stream failure.
        if session_id:
            append_turn(session_id, "user", req.question)

        try:
            state = await agent.ainvoke(
                {
                    "question": req.question,
                    "user_id": user_id,
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
                "session_id": req.session_id,
                "resolved_question": state.get("resolved_question"),
                "route": state.get("route"),
                "grade": state.get("grade"),
                "attempt": state.get("attempt"),
                "trace": state.get("trace", []),
                "sources": [s.model_dump() for s in _sources(docs)],
                "retrieved_chunks": len(docs),
                "history_turns_loaded": len(history),
            }
        )

        full_answer_parts: list[str] = []
        async for event in synthesize_streaming(state):
            if event.get("type") == "token":
                full_answer_parts.append(event.get("content", ""))
            yield _sse(event)

        # Persist the assistant turn ONLY after a complete answer; a
        # half-streamed answer corrupted by an error frame is not safe to save.
        full_answer = "".join(full_answer_parts)
        if session_id and full_answer:
            append_turn(session_id, "assistant", full_answer)

        yield _sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.delete("/sessions/{session_id}")
def clear_session_endpoint(session_id: str, user_id: str = CurrentUser) -> dict:
    """Clear this user's conversation. A session id belonging to someone else
    resolves to a different namespaced key and removes nothing."""
    removed = clear_session(_scoped_session(user_id, session_id))
    return {"session_id": session_id, "turns_removed": removed}


@router.get("/sessions/{session_id}/turns")
def get_session_turns_endpoint(
    session_id: str, limit: int = 50, user_id: str = CurrentUser
) -> dict:
    """Stored turns, oldest-first, for chat hydration on reload."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    turns = get_recent_turns(_scoped_session(user_id, session_id), n=limit)
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
