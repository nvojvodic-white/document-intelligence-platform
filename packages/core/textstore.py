"""Extracted text on disk, addressed by content hash.

Kept out of SQLite so the single writer is not pushing hundreds of kilobytes
around. Storing it lets a chunking change reuse extraction: bump
CHUNKING_VERSION and documents re-chunk without re-downloading anything.
"""
from __future__ import annotations

from pathlib import Path

from core.config import TEXT_STORE_DIR


def _relative_path(sha256: str) -> Path:
    # Fan out on the first two hex chars; flat directories of tens of
    # thousands of files list slowly.
    return Path(sha256[:2]) / f"{sha256}.txt"


def write_text(sha256: str, text: str) -> str:
    """Store extracted text. Returns the ref to record on the blob."""
    rel = _relative_path(sha256)
    path = TEXT_STORE_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline='' so stored bytes match the extraction on any platform.
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return str(rel).replace("\\", "/")


def read_text(text_ref: str) -> str | None:
    """Stored text, or None if missing - the caller should re-extract rather
    than fail the run."""
    path = TEXT_STORE_DIR / text_ref
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return f.read()
    except FileNotFoundError:
        return None
