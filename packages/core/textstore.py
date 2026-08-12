"""Extracted text on disk, addressed by content hash.

Kept out of SQLite so the row stays small and the write path does not push
hundreds of kilobytes through the single writer the sync worker shares with
the API's readers. `blobs.extracted_text_ref` holds the relative path written
here.

Storing extracted text at all is what lets a chunking change reuse extraction:
bump CHUNKING_VERSION and the documents are re-chunked and re-embedded without
re-downloading a single object from the provider.
"""
from __future__ import annotations

from pathlib import Path

from core.config import TEXT_STORE_DIR


def _relative_path(sha256: str) -> Path:
    # Fan out on the first two hex characters. A flat directory of tens of
    # thousands of files is slow to list on most filesystems.
    return Path(sha256[:2]) / f"{sha256}.txt"


def write_text(sha256: str, text: str) -> str:
    """Store extracted text. Returns the ref to record on the blob."""
    rel = _relative_path(sha256)
    path = TEXT_STORE_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline='' so the stored bytes match what was extracted, on any platform.
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return str(rel).replace("\\", "/")


def read_text(text_ref: str) -> str | None:
    """Read stored text back, or None if it has gone missing.

    Returns None rather than raising: a missing text file means the cache is
    incomplete, and the caller's correct response is to re-extract, not to fail
    the run.
    """
    path = TEXT_STORE_DIR / text_ref
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return f.read()
    except FileNotFoundError:
        return None
