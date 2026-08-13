"""Chat over the caller's own knowledge base.

Every handler takes user_id from the verified token and puts it into the graph
state; retrieval reads it from there. There is no fallback, so a bug that
dropped it errors rather than silently searching someone else's documents.
"""
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.deps import CurrentUser
from app.rag.agent.graph import get_agent
from app.rag.agent.graph_streaming import (
    get_streaming_agent_with_memory,
    synthesize_streaming,
)
from app.rag.memory.store import append_turn, clear_session, get_recent_turns
from app.rag.schemas import QueryRequest, QueryResponse, Source, StreamQueryRequest

MEMORY_WINDOW_SIZE = 6  # 3 user/assistant pairs
SNIPPET_CHARS = 200

router = APIRouter()


def _sse(payload: dict) -> str:
    # The trailing blank line is required; without it clients never flush.
    return f"data: {json.dumps(payload)}\n\n"


def _scoped_session(user_id: str, session_id: str | None) -> str | None:
    """Namespace client-supplied session ids by user, so guessing one reads
    nothing."""
    return f"{user_id}:{session_id}" if session_id else None


def _sources(docs) -> list[Source]:
    return [
        Source(
            title=d.metadata.get("title", "unknown"),
            url=d.metadata.get("url", ""),
            source=d.metadata.get("source", "unknown"),
            file_id=d.metadata.get("file_id", ""),
            chunk_id=d.metadata.get("chunk_id", ""),
            snippet=d.page_content[:SNIPPET_CHARS]
            + ("..." if len(d.page_content) > SNIPPET_CHARS else ""),
        )
        for d in docs
    ]


@router.post("/agent_query", response_model=QueryResponse)
def agent_query(req: QueryRequest, user_id: str = CurrentUser) -> QueryResponse:
    """Non-streaming: classify -> retrieve -> grade -> [rewrite] -> synthesize."""
    result = get_agent().invoke({"question": req.question, "user_id": user_id})
    docs = result.get("documents", [])
    return QueryResponse(
        answer=result.get("answer", ""),
        sources=_sources(docs),
        retrieved_chunks=len(docs),
    )


@router.post("/agent_query_stream")
async def agent_query_stream(
    req: StreamQueryRequest, user_id: str = CurrentUser
) -> StreamingResponse:
    """Streaming, multi-turn. Emits one metadata frame (route, grade, sources),
    then synthesis tokens, then done.

    With no session_id it degrades to single-turn. The user's turn is persisted
    before the LLM runs so it survives a mid-stream failure; the assistant's is
    persisted only after a complete answer.
    """
    agent = get_streaming_agent_with_memory()
    session_id = _scoped_session(user_id, req.session_id)

    history: list[dict] = []
    if session_id:
        history = [
            t.to_dict() for t in get_recent_turns(session_id, n=MEMORY_WINDOW_SIZE)
        ]

    async def event_stream():
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
                "route": state.get("route"),
                "grade": state.get("grade"),
                "attempt": state.get("attempt"),
                "trace": state.get("trace", []),
                "sources": [s.model_dump() for s in _sources(docs)],
                "retrieved_chunks": len(docs),
                "history_turns_loaded": len(history),
            }
        )

        parts: list[str] = []
        async for event in synthesize_streaming(state):
            if event.get("type") == "token":
                parts.append(event.get("content", ""))
            yield _sse(event)

        answer = "".join(parts)
        if session_id and answer:
            append_turn(session_id, "assistant", answer)

        yield _sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.delete("/sessions/{session_id}")
def clear_session_endpoint(session_id: str, user_id: str = CurrentUser) -> dict:
    removed = clear_session(_scoped_session(user_id, session_id))
    return {"session_id": session_id, "turns_removed": removed}


@router.get("/sessions/{session_id}/turns")
def get_session_turns(
    session_id: str, limit: int = 50, user_id: str = CurrentUser
) -> dict:
    """Prior turns, oldest first, for rehydrating the chat on mount."""
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
