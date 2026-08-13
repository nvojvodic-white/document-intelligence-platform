"""Embedding providers, selected by EMBEDDING_PROVIDER.

  openai  (default) text-embedding-3-small. Chat goes to Claude, so this is a
          second provider and a second key.
  hash    Deterministic offline embedder for tests: feature-hashed token
          n-grams, L2 normalised. Identical documents embed identically, which
          is all the isolation tests need.

`hash` is opt-in and never a fallback. A missing key raises rather than quietly
degrading - silently swapping models would write poisoned vectors under a
version string claiming otherwise.
"""
from __future__ import annotations

import hashlib
import os
import re

from core.config import EMBEDDING_MODEL

_HASH_DIM = 256
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class EmbeddingError(Exception):
    pass


def provider_name() -> str:
    return os.getenv("EMBEDDING_PROVIDER", "openai").lower()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed documents. Order of the result matches the order of the input."""
    if not texts:
        return []
    if provider_name() == "hash":
        return [_hash_embed(t) for t in texts]
    return _openai_embed(texts)


def embed_query(text: str) -> list[float]:
    """Embed a query. Same provider as embed_texts, or the vectors are not
    comparable."""
    return embed_texts([text])[0]


def dimension() -> int:
    if provider_name() == "hash":
        return _HASH_DIM
    # Only used to record `dim`; the real length comes from the vectors.
    return 1536


# --- openai -----------------------------------------------------------------

_client = None


def _openai_embed(texts: list[str]) -> list[list[float]]:
    global _client
    if not os.getenv("OPENAI_API_KEY"):
        raise EmbeddingError(
            "OPENAI_API_KEY is not set. Embeddings use OpenAI while chat uses "
            "Anthropic, so both keys are required. EMBEDDING_PROVIDER=hash is "
            "for tests only."
        )
    if _client is None:
        from langchain_openai import OpenAIEmbeddings

        _client = OpenAIEmbeddings(model=EMBEDDING_MODEL)
    return _client.embed_documents(texts)


# --- deterministic test provider --------------------------------------------


def _hash_embed(text: str) -> list[float]:
    """Feature-hash tokens and bigrams into a fixed-size vector."""
    vec = [0.0] * _HASH_DIM
    tokens = _TOKEN_RE.findall(text.lower())
    features = tokens + [
        f"{a}_{b}" for a, b in zip(tokens, tokens[1:])
    ]
    for feature in features:
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % _HASH_DIM
        # Sign from a separate byte so collisions cancel, not compound.
        vec[index] += 1.0 if digest[4] & 1 else -1.0

    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0.0:
        # Cosine space cannot rank a zero vector.
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]
