"""Text to chunks.

Parameters and separators are carried over from the pre-fork retriever
comparison rather than re-derived: 800/120 balances definitional coherence
(entity definitions cluster in 200-400 char spans) against event-narrative
continuity (paragraphs run ~600-1000 chars).

Chunking is a pure function of the text, so it is cacheable by sha256 like
extraction. CHUNKING_VERSION is folded into EMBEDDING_VERSION because chunks
are what gets embedded - re-chunking necessarily invalidates vectors, and
having one key for both stops the two caches from disagreeing.
"""
from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.config import CHUNK_OVERLAP, CHUNK_SEPARATORS, CHUNK_SIZE

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=CHUNK_SEPARATORS,
)


def chunk_text(text: str) -> list[str]:
    """Split extracted text into chunk texts, in document order.

    The list index is the chunk's ordinal, and (sha256, ordinal) is its
    identity everywhere else in the system - in the chunks table, in the
    embedding cache, and as the vector id in each user's collection.
    """
    return [c for c in _splitter.split_text(text) if c.strip()]
