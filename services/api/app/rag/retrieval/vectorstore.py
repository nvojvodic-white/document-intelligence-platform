import copy
import pickle
import re
from functools import lru_cache

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores import VectorStoreRetriever
from langchain_openai import OpenAIEmbeddings

load_dotenv()

CHROMA_DIR = "data/chroma_middle_earth"
COLLECTION = "middle_earth"
CHUNKS_PATH = "data/chunks_middle_earth.pkl"

# BM25's default preprocessor is text.split(): no lowercasing, no punctuation
# stripping, no stopword removal. That makes "Gandalf?" != "Gandalf" and lets
# common words ("who", "is") dominate scoring. Lowercase, strip punctuation,
# and drop a small English stopword set so rare named entities carry the signal.
_BM25_STOPWORDS = frozenset(
    "a an and are as at be by for from has he in is it its of on that the to "
    "was were will with who what when where which why how do does did this "
    "these those there their them they i you we".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _bm25_preprocess(text: str) -> list[str]:
    return [
        tok
        for tok in _TOKEN_RE.findall(text.lower())
        if tok not in _BM25_STOPWORDS
    ]


@lru_cache(maxsize=1)
def get_vectorstore() -> Chroma:
    return Chroma(
        collection_name=COLLECTION,
        embedding_function=OpenAIEmbeddings(model="text-embedding-3-small"),
        persist_directory=CHROMA_DIR,
    )


@lru_cache(maxsize=1)
def _load_chunks() -> tuple[Document, ...]:
    with open(CHUNKS_PATH, "rb") as f:
        return tuple(pickle.load(f))


@lru_cache(maxsize=1)
def _bm25_base() -> BM25Retriever:
    # Building BM25 over ~5.7k chunks takes a second or two; cache the index
    # and set .k per request below. Custom preprocessor (lowercase / strip
    # punctuation / drop stopwords) so named-entity tokens drive the ranking.
    return BM25Retriever.from_documents(
        list(_load_chunks()), preprocess_func=_bm25_preprocess
    )


def get_dense_retriever(k: int = 4) -> VectorStoreRetriever:
    return get_vectorstore().as_retriever(search_kwargs={"k": k})


def get_sparse_retriever(k: int = 4) -> BM25Retriever:
    # Shallow-copy the cached retriever so per-request .k mutations don't race
    # against each other (the underlying BM25 index is shared, not rebuilt).
    retriever = copy.copy(_bm25_base())
    retriever.k = k
    return retriever


class _TopKEnsembleRetriever(EnsembleRetriever):
    """EnsembleRetriever whose RRF fusion is truncated to top_k.

    EnsembleRetriever returns the deduped union of all sub-retriever candidates,
    so feeding it 2k from each retriever yields up to ~4k docs. We want each
    sub-retriever to surface 2k candidates (so RRF can rescue docs ranked low in
    one list) but return only the fused top_k. invoke() routes through
    rank_fusion (not _get_relevant_documents), so truncate there.
    """

    top_k: int = 4

    def rank_fusion(self, query, run_manager, *args, **kwargs):
        return super().rank_fusion(query, run_manager, *args, **kwargs)[: self.top_k]


def get_hybrid_retriever(
    k: int = 4,
    dense_weight: float = 0.5,
    sparse_weight: float = 0.5,
) -> _TopKEnsembleRetriever:
    # Each sub-retriever surfaces 2k candidates; RRF fuses, then truncate to k.
    dense = get_dense_retriever(k=k * 2)
    sparse = get_sparse_retriever(k=k * 2)
    return _TopKEnsembleRetriever(
        retrievers=[dense, sparse],
        weights=[dense_weight, sparse_weight],
        top_k=k,
    )


def get_retriever(k: int = 4, kind: str = "dense") -> BaseRetriever:
    """Swappable retriever factory.

    kind in {dense, sparse, hybrid (50/50), hybrid_40_60, hyde, multi_query}.

    The semantic / pdr / turbovec kinds were dropped in the multi-tenant fork:
    each needs a second prebuilt index of its own, so per-user routing would
    mean a second embedding pass per user at sync time. The kinds kept here
    reach the same routing variety at no extra embedding cost - sparse builds
    from the user's own chunk rows, and hyde / multi_query are query transforms
    over the user's dense collection.
    """
    if kind == "sparse":
        return get_sparse_retriever(k=k)
    if kind == "hybrid":
        return get_hybrid_retriever(k=k, dense_weight=0.5, sparse_weight=0.5)
    if kind == "hybrid_40_60":
        return get_hybrid_retriever(k=k, dense_weight=0.4, sparse_weight=0.6)
    if kind == "hyde":
        from app.rag.retrieval.hyde import get_hyde_retriever

        return get_hyde_retriever(k=k)
    if kind == "multi_query":
        from app.rag.retrieval.multi_query import get_multi_query_retriever

        return get_multi_query_retriever(k=k)
    return get_dense_retriever(k=k)
