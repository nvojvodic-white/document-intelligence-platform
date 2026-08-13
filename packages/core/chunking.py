"""Text to chunks.

A pure function of the text, so cacheable by sha256 like extraction.
CHUNKING_VERSION folds into EMBEDDING_VERSION because chunks are what gets
embedded - one key for both stops the two caches disagreeing.
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
    """Split text into chunks, in document order. The list index is the
    ordinal, and (sha256, ordinal) is a chunk's identity everywhere else."""
    return [c for c in _splitter.split_text(text) if c.strip()]
