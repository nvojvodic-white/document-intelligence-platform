from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    k: int = Field(default=4, ge=1, le=20)


class StreamQueryRequest(BaseModel):
    """Request shape for /agent_query_stream_v2. session_id is optional; when
    absent the endpoint behaves like /agent_query_stream (no memory)."""

    question: str = Field(..., min_length=1, max_length=500)
    session_id: str | None = Field(default=None, max_length=128)
    k: int = Field(default=4, ge=1, le=20)


class HistoryTurn(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., max_length=10000)


class Source(BaseModel):
    title: str
    url: str
    source: str
    snippet: str


class QueryResponse(BaseModel):
    answer: str
    sources: list[Source]
    retrieved_chunks: int
