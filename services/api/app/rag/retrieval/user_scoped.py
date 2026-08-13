"""Retrieval, scoped to one user's knowledge base.

Two stages on purpose: search returns chunk ids and scores with no text, then
get_chunk_texts() resolves them against user_blobs. A vector surfacing from
another tenant resolves to nothing and drops out before reaching a prompt.

Three kinds, none needing a second embedded index: dense over the user's
collection, sparse from BM25 over their chunk rows, hybrid fusing both.
"""
from __future__ import annotations

import re
import threading
from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForRetrieverRun,
    CallbackManagerForRetrieverRun,
)
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from rank_bm25 import BM25Okapi

from core import repositories as repo
from core import vectors

# BM25's default is text.split(), which makes "Gandalf?" != "Gandalf" and lets
# common words dominate.
_BM25_STOPWORDS = frozenset(
    "a an and are as at be by for from has he in is it its of on that the to "
    "was were will with who what when where which why how do does did this "
    "these those there their them they i you we".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# 60 is the value from the RRF paper.
_RRF_K = 60


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _BM25_STOPWORDS]


# --- per-user sparse index --------------------------------------------------

_bm25_cache: dict[str, tuple[int, BM25Okapi, list[dict]]] = {}
_bm25_lock = threading.Lock()


def _sparse_index(user_id: str):
    """BM25 over this user's chunks, rebuilt when the chunk count changes.

    Count is a cheap invalidation signal at this size; a production build wants
    a version column, and this is the line to change.
    """
    chunks = repo.get_user_chunks(user_id)
    with _bm25_lock:
        cached = _bm25_cache.get(user_id)
        if cached and cached[0] == len(chunks):
            return cached[1], cached[2]
        if not chunks:
            return None, []
        index = BM25Okapi([_tokenize(c["text"]) for c in chunks])
        _bm25_cache[user_id] = (len(chunks), index, chunks)
        return index, chunks


def _sparse_hits(user_id: str, query: str, k: int) -> list[dict[str, Any]]:
    index, chunks = _sparse_index(user_id)
    if index is None:
        return []
    scores = index.get_scores(_tokenize(query))
    ranked = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)[:k]
    return [
        {
            "chunk_id": chunks[i]["chunk_id"],
            "sha256": chunks[i]["sha256"],
            "ordinal": chunks[i]["ordinal"],
            "score": float(scores[i]),
        }
        for i in ranked
        if scores[i] > 0
    ]


# --- fusion -----------------------------------------------------------------


def _rrf(*rankings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reciprocal-rank fusion: position only, so cosine distances and BM25's
    unbounded scores never need to be made comparable."""
    scored: dict[str, float] = {}
    seen: dict[str, dict[str, Any]] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking):
            cid = hit["chunk_id"]
            scored[cid] = scored.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
            seen.setdefault(cid, hit)
    order = sorted(scored, key=lambda cid: scored[cid], reverse=True)
    return [seen[cid] for cid in order]


# --- the retrieval entrypoint -----------------------------------------------


def retrieve(user_id: str, query: str, k: int = 4, kind: str = "dense") -> list[Document]:
    """Search one user's knowledge base and return citable documents."""
    if kind == "sparse":
        hits = _sparse_hits(user_id, query, k)
    elif kind in ("hybrid", "hybrid_40_60"):
        # 2k per side so fusion can rescue a chunk one retriever ranked low.
        hits = _rrf(
            vectors.query(user_id, query, k=k * 2),
            _sparse_hits(user_id, query, k * 2),
        )[:k]
    else:
        hits = vectors.query(user_id, query, k=k)

    return _to_documents(user_id, hits)


def _to_documents(user_id: str, hits: list[dict[str, Any]]) -> list[Document]:
    """Resolve hits to documents, dropping anything this user cannot read.

    A hit whose text does not resolve is dropped rather than rendered as a
    blank citation - fewer citations is the right degradation.
    """
    if not hits:
        return []
    texts = repo.get_chunk_texts(user_id, [h["chunk_id"] for h in hits])
    attribution = repo.get_attribution(
        user_id, [h.get("sha256") for h in hits if h.get("sha256")]
    )

    documents: list[Document] = []
    for hit in hits:
        text = texts.get(hit["chunk_id"])
        if not text:
            continue
        source = attribution.get(hit.get("sha256") or "", {})
        provider_key = source.get("provider_key", "")
        documents.append(
            Document(
                page_content=text,
                metadata={
                    # Cite the file the user recognises, not the sha256.
                    "title": provider_key.rsplit("/", 1)[-1] or "unknown",
                    "source": provider_key or "unknown",
                    "url": "",
                    "file_id": source.get("file_id", ""),
                    "chunk_id": hit["chunk_id"],
                    "sha256": hit.get("sha256", ""),
                    "ordinal": hit.get("ordinal", 0),
                },
            )
        )
    return documents


class UserScopedRetriever(BaseRetriever):
    """LangChain adapter. user_id is bound at construction from the verified
    token, never taken from the query, so no phrasing widens the search."""

    user_id: str
    k: int = 4
    kind: str = "dense"

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        return retrieve(self.user_id, query, self.k, self.kind)

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun
    ) -> list[Document]:
        # Both are CPU-bound and fast here, so inline beats a thread hop.
        return retrieve(self.user_id, query, self.k, self.kind)


def get_user_retriever(
    user_id: str, k: int = 4, kind: str = "dense"
) -> UserScopedRetriever:
    """The only way to build a retriever - there is no variant without a
    user_id."""
    if not user_id:
        raise ValueError("retrieval requires a user_id")
    return UserScopedRetriever(user_id=user_id, k=k, kind=kind)
