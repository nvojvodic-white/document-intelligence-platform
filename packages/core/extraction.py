"""Bytes to text.

Markdown and plain text only. The seam is here rather than the capability:
extract() dispatches on suffix, so a new type is a branch plus a bump of
EXTRACTION_VERSION. Unsupported types raise ExtractionError, which the worker
records as a per-file failure rather than aborting the run.
"""
from __future__ import annotations

from core.config import EXTRACTION_VERSION

__all__ = ["ExtractionError", "extract", "EXTRACTION_VERSION", "SUPPORTED_SUFFIXES"]

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".text"}


class ExtractionError(Exception):
    """Raised when bytes cannot be turned into text."""


def extract(provider_key: str, data: bytes) -> str:
    """Extract text. provider_key only picks the parser - the result depends on
    the bytes alone, which is what makes extraction cacheable by sha256."""
    suffix = _suffix(provider_key)
    if suffix not in SUPPORTED_SUFFIXES:
        raise ExtractionError(
            f"unsupported file type {suffix or '(none)'!r}; "
            f"supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ExtractionError(f"not valid UTF-8: {e}") from e

    # A CRLF copy is correctly a different blob, but its text should chunk
    # identically to its LF twin.
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ExtractionError("document is empty after extraction")
    return text


def _suffix(key: str) -> str:
    name = key.rsplit("/", 1)[-1]
    _, dot, ext = name.rpartition(".")
    return f".{ext.lower()}" if dot else ""
