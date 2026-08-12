"""Embedding providers.

Two, selected by EMBEDDING_PROVIDER:

  openai  (default) text-embedding-3-small. OpenRouter has no embeddings API,
          so this is the one call that does not go through it. Stated in the
          README rather than glossed - a clean clone needs two keys.

  hash    A deterministic offline embedder for tests. Feature-hashed character
          n-grams, L2 normalised. It is a real function of the text, so
          identical documents embed identically and lexically similar ones
          score closer, which is all the isolation tests need.

`hash` is opt-in and never a fallback. If the OpenAI key is missing the openai
provider raises instead of quietly degrading: a platform that silently swaps
its embedding model would return plausible, badly-grounded answers, and the
vectors it wrote would be poisoned under a version string claiming otherwise.
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
    """Embed a query. Must use the same provider and model as embed_texts, or
    query vectors and stored vectors are not comparable."""
    return embed_texts([text])[0]


def dimension() -> int:
    if provider_name() == "hash":
        return _HASH_DIM
    # text-embedding-3-small. Only used to record `dim` alongside cached
    # vectors; the true length is taken from the vectors themselves.
    return 1536


# --- openai -----------------------------------------------------------------

_client = None


def _openai_embed(texts: list[str]) -> list[list[float]]:
    global _client
    if not os.getenv("OPENAI_API_KEY"):
        raise EmbeddingError(
            "OPENAI_API_KEY is not set. Embeddings do not go through "
            "OpenRouter (it has no embeddings API), so this key is required "
            "in addition to OPENROUTER_API_KEY. Set EMBEDDING_PROVIDER=hash "
            "only for tests."
        )
    if _client is None:
        from langchain_openai import OpenAIEmbeddings

        _client = OpenAIEmbeddings(model=EMBEDDING_MODEL)
    return _client.embed_documents(texts)


# --- deterministic test provider --------------------------------------------


def _hash_embed(text: str) -> list[float]:
    """Feature-hash tokens and their bigrams into a fixed-dimension vector."""
    vec = [0.0] * _HASH_DIM
    tokens = _TOKEN_RE.findall(text.lower())
    features = tokens + [
        f"{a}_{b}" for a, b in zip(tokens, tokens[1:])
    ]
    for feature in features:
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % _HASH_DIM
        # Sign from a separate byte so collisions cancel rather than compound.
        vec[index] += 1.0 if digest[4] & 1 else -1.0

    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0.0:
        # An empty or token-free document. Return a unit vector rather than
        # zeros: Chroma's cosine space cannot rank a zero vector.
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]
