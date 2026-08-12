"""Content identity.

"The same file" means the same bytes. Not the same path, not the same name,
not the same provider key - those are attribution, and they live in the `files`
table. Two documents with different names in different directories owned by
different users are one blob if their bytes hash equally.
"""
import hashlib

# Hash in blocks so a large object never has to be held in memory twice.
_BLOCK = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(stream) -> tuple[str, int]:
    """Hash a file-like object. Returns (hex digest, byte count)."""
    digest = hashlib.sha256()
    total = 0
    while True:
        block = stream.read(_BLOCK)
        if not block:
            break
        digest.update(block)
        total += len(block)
    return digest.hexdigest(), total
