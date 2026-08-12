"""Retrieval, scoped to one user's knowledge base.

Replaces the pre-fork global retrievers. The old ones read a single Chroma
collection and a single pickled chunk list built at deploy time; both are
gone, because there is no such thing as "the corpus" any more - there is only
some user's corpus.

The retrieval path is deliberately in two stages:

  1. Search returns chunk ids and scores. No text.
  2. get_chunk_texts(user_id, ids) resolves those ids to text, re-checking
     possession against user_blobs.

So a vector that somehow surfaced from another tenant's collection resolves to
nothing and drops out before it can reach a prompt. Documents are only
constructed for ids that survive step two.

Three kinds survive the fork, none of which needs a second embedded index:
  dense   the user's Chroma collection
  sparse  BM25 built from the user's own chunk rows
  hybrid  reciprocal-rank fusion of the two
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

# Same preprocessing as the pre-fork sparse retriever: BM25's default is
# text.split(), which makes "Gandalf?" != "Gandalf" and lets common words
# dominate scoring.
_BM25_STOPWORDS = frozenset(
    "a an and are as at be by for from has he in is it its of on that the to "
    "was were will with who what when where which why how do does did this "
    "these those there their them they i you we".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# RRF constant. 60 is the value from the original paper and the pre-fork build.
_RRF_K = 60


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _BM25_STOPWORDS]


# --- per-user sparse index --------------------------------------------------

_bm25_cache: dict[str, tuple[int, BM25Okapi, list[dict]]] = {}
_bm25_lock = threading.Lock()


def _sparse_index(user_id: str):
    """BM25 over this user's chunks, rebuilt when their chunk count changes.

    Keyed on the row count rather than a timestamp: a sync that adds or removes
    content changes the count, which is a cheap and sufficient invalidation
    signal for a demo-sized corpus. A production build would want a version
    column, and this is the line to change.
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
    """Reciprocal-rank fusion. Rank position only, so the dense retriever's
    distances and BM25's unbounded scores never have to be made comparable."""
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
        # Each side surfaces 2k candidates so fusion can rescue a chunk ranked
        # low by one retriever, then the fused list is truncated to k.
        hits = _rrf(
            vectors.query(user_id, query, k=k * 2),
            _sparse_hits(user_id, query, k * 2),
        )[:k]
    else:
        hits = vectors.query(user_id, query, k=k)

    return _to_documents(user_id, hits)


def _to_documents(user_id: str, hits: list[dict[str, Any]]) -> list[Document]:
    """Resolve hits to documents, dropping anything this user cannot read.

    Both lookups are user-scoped. A hit whose text does not resolve is silently
    dropped rather than rendered as an empty citation: fewer citations is the
    correct degradation, a blank one is not.
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
                    # Citations point at the source file, which is what the
                    # user recognises - not at the sha256 that identifies it.
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
    """LangChain adapter, so the inherited graph keeps working unchanged.

    The user_id is bound when the retriever is constructed, from the verified
    token. It is never taken from the query, so there is no way to phrase a
    question that widens the search.
    """

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
        # Chroma's query and BM25 scoring are both CPU-bound and fast at this
        # corpus size, so running them inline is simpler than a thread hop and
        # does not hold the loop for long.
        return retrieve(self.user_id, query, self.k, self.kind)


def get_user_retriever(
    user_id: str, k: int = 4, kind: str = "dense"
) -> UserScopedRetriever:
    """The only way to build a retriever. Takes user_id first, and there is no
    variant that omits it."""
    if not user_id:
        raise ValueError("retrieval requires a user_id")
    return UserScopedRetriever(user_id=user_id, k=k, kind=kind)
