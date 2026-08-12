"""Bytes to text.

Deliberately small. The corpus is markdown and plain text, so extraction is a
decode plus a light normalisation, and adding PDF/DOCX parsing would have cost
an hour to serve documents this build does not have. The seam is here rather
than the capability: extract() dispatches on the key's suffix, so a new type is
a new branch and a bump of EXTRACTION_VERSION.

Unsupported types raise ExtractionError, which the sync worker records as a
per-file failure. The run ends `partial` and the other files still index -
one bad file does not abort a sync.
"""
from __future__ import annotations

from core.config import EXTRACTION_VERSION

__all__ = ["ExtractionError", "extract", "EXTRACTION_VERSION", "SUPPORTED_SUFFIXES"]

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".text"}


class ExtractionError(Exception):
    """Raised when bytes cannot be turned into text."""


def extract(provider_key: str, data: bytes) -> str:
    """Extract text from an object's raw bytes.

    provider_key is used only to pick a parser. The text returned depends on
    the bytes alone, which is what makes extraction cacheable by sha256 across
    users - the same bytes yield the same text regardless of what anyone
    named the file.
    """
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

    # Normalise line endings so a CRLF copy of a document chunks identically to
    # its LF twin. Their bytes differ, so they are correctly two blobs, but
    # their extracted text should not differ in ways that change retrieval.
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ExtractionError("document is empty after extraction")
    return text


def _suffix(key: str) -> str:
    name = key.rsplit("/", 1)[-1]
    _, dot, ext = name.rpartition(".")
    return f".{ext.lower()}" if dot else ""
