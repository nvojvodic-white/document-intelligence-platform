"""Per-user vector storage and the embedding cache that fills it.

Two independent isolation mechanisms, because each is a single point of failure
alone:

  1. One collection per user. _collection_name() is the only place a name is
     built, and no function here accepts one, so no caller can address another
     tenant's collection by mistake.
  2. user_id on every stored vector plus a where filter on every query, which
     still holds if the first is ever defeated.

Vectors are computed once per (sha256, ordinal, embedding_version) and COPIED
per user - never shared rows. The saving is the embedding call, not storage.
"""
from __future__ import annotations

import re
import threading
from typing import Any

import chromadb
import numpy as np

from core import repositories as repo
from core.config import CHROMA_DIR, EMBEDDING_VERSION
from core.embeddings import dimension, embed_query, embed_texts

_client = None
_client_lock = threading.Lock()

# Chroma collection names must be 3-63 chars of [a-zA-Z0-9._-] and start and
# end alphanumeric.
_SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,40}$")


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            CHROMA_DIR.mkdir(parents=True, exist_ok=True)
            _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return _client


def _collection_name(user_id: str) -> str:
    """The only place a collection name is built.

    Rejects odd ids rather than escaping them: user_id comes from a verified
    token, so anything exotic means something upstream is broken.
    """
    if not _SAFE_ID.match(user_id or ""):
        raise ValueError(f"refusing to build a collection name for {user_id!r}")
    return f"kb_user_{user_id}"


def _collection(user_id: str):
    return _get_client().get_or_create_collection(
        name=_collection_name(user_id),
        metadata={"hnsw:space": "cosine"},
    )


def _chunk_id(sha256: str, ordinal: int) -> str:
    return f"{sha256}:{ordinal}"


def _pack(vector) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def _unpack(raw: bytes) -> list[float]:
    return np.frombuffer(raw, dtype=np.float32).tolist()


def index_blob_for_user(
    user_id: str, sha256: str, chunk_texts: list[str]
) -> dict[str, int]:
    """Put a blob's chunks into this user's collection.

    Returns {"embedded": n, "from_cache": n} - the counters that make the dedup
    claim checkable rather than asserted.
    """
    if not chunk_texts:
        return {"embedded": 0, "from_cache": 0}

    cached = repo.get_cached_vectors(sha256, EMBEDDING_VERSION)
    missing = [i for i in range(len(chunk_texts)) if i not in cached]

    fresh: dict[int, list[float]] = {}
    if missing:
        computed = embed_texts([chunk_texts[i] for i in missing])
        fresh = dict(zip(missing, computed))
        repo.put_cached_vectors(
            sha256,
            EMBEDDING_VERSION,
            {i: _pack(v) for i, v in fresh.items()},
            dim=len(next(iter(fresh.values()))) if fresh else dimension(),
        )

    vectors = [
        fresh[i] if i in fresh else _unpack(cached[i])
        for i in range(len(chunk_texts))
    ]

    # Upsert, not add, so a re-sync of unchanged content is idempotent.
    _collection(user_id).upsert(
        ids=[_chunk_id(sha256, i) for i in range(len(chunk_texts))],
        embeddings=vectors,
        documents=chunk_texts,
        metadatas=[
            {"user_id": user_id, "sha256": sha256, "ordinal": i}
            for i in range(len(chunk_texts))
        ],
    )
    return {"embedded": len(missing), "from_cache": len(chunk_texts) - len(missing)}


def drop_blob_for_user(user_id: str, sha256: str) -> int:
    """Remove a blob's chunks from this user's collection.

    Called only once no other live file row of theirs references these bytes.
    The blob and cache survive - they are content, not attribution.
    """
    collection = _collection(user_id)
    existing = collection.get(where={"sha256": sha256}, include=[])
    ids = existing.get("ids", [])
    if ids:
        collection.delete(ids=ids)
    return len(ids)


def query(user_id: str, question: str, k: int = 4) -> list[dict[str, Any]]:
    """Search this user's knowledge base.

    Returns chunk ids and scores, never text; get_chunk_texts() resolves those
    against user_blobs, so a stray vector cannot become visible text.
    """
    if k <= 0:
        return []
    collection = _collection(user_id)
    if collection.count() == 0:
        return []

    result = collection.query(
        query_embeddings=[embed_query(question)],
        n_results=min(k, collection.count()),
        # Mechanism two, independent of the per-user collection above.
        where={"user_id": user_id},
        include=["metadatas", "distances"],
    )

    ids = (result.get("ids") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    return [
        {
            "chunk_id": cid,
            "sha256": (meta or {}).get("sha256"),
            "ordinal": (meta or {}).get("ordinal"),
            "distance": dist,
        }
        for cid, dist, meta in zip(ids, distances, metadatas)
    ]


def collection_size(user_id: str) -> int:
    return _collection(user_id).count()


