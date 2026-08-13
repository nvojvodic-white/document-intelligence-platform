"""Content identity.

"The same file" means the same bytes - not the same path, name, or provider
key. Those are attribution and live in the files table.
"""
import hashlib


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
