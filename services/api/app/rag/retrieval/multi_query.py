"""Multi-query retriever: generate several phrasings of the question, retrieve
for each, deduplicate the union.

Targets the recall-ceiling failure mode: a single phrasing may miss chunks that
a slightly different phrasing would catch. LangChain's MultiQueryRetriever
generates ~3 variants, retrieves k per variant, and dedupes by content - so the
fused set is larger than k. We truncate back to k for a fair RAGAS comparison
(same context budget as dense/hybrid/hyde), trading "same compute" for "same
context size".
"""
import numpy as np
from langchain_anthropic import ChatAnthropic
from langchain_classic.retrievers.multi_query import MultiQueryRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from app.rag.retrieval.vectorstore import get_dense_retriever, get_vectorstore


class TopKMultiQueryRetriever(BaseRetriever):
    """Wrap MultiQueryRetriever, then keep the top_k chunks most relevant to the
    ORIGINAL query.

    MultiQueryRetriever returns the deduped union grouped by variant in
    generation order, NOT globally relevance-ranked. Naively slicing [:k] keeps
    a weak variant's chunks and drops good ones that landed later. Instead, embed
    the original query and rank the union by cosine similarity to it, then take
    top_k - a fair same-context-budget comparison against dense/hyde.
    """

    inner: MultiQueryRetriever
    top_k: int = 4

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        docs = self.inner.invoke(query)
        if len(docs) <= self.top_k:
            return docs
        emb = get_vectorstore().embeddings
        q_vec = np.array(emb.embed_query(query))
        d_vecs = np.array(emb.embed_documents([d.page_content for d in docs]))
        sims = d_vecs @ q_vec / (
            np.linalg.norm(d_vecs, axis=1) * np.linalg.norm(q_vec) + 1e-9
        )
        ranked = sorted(zip(docs, sims), key=lambda x: x[1], reverse=True)
        return [d for d, _ in ranked[: self.top_k]]


def get_multi_query_retriever(k: int = 4) -> TopKMultiQueryRetriever:
    base = get_dense_retriever(k=k)  # k per generated variant
    llm = ChatAnthropic(model="claude-sonnet-4-5", max_tokens=300, max_retries=5)
    inner = MultiQueryRetriever.from_llm(retriever=base, llm=llm)
    return TopKMultiQueryRetriever(inner=inner, top_k=k)
