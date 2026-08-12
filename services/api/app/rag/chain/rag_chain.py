from functools import lru_cache

from langchain_anthropic import ChatAnthropic
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable, RunnableParallel, RunnablePassthrough

from app.rag.chain.prompts import rag_prompt
from app.rag.retrieval.vectorstore import get_retriever


def _build_llm() -> ChatAnthropic:
    return ChatAnthropic(model="claude-sonnet-4-5", max_tokens=1024)


def format_docs_for_prompt(docs: list[Document]) -> str:
    """Render retrieved docs as numbered context entries for the prompt."""
    blocks = []
    for i, doc in enumerate(docs, start=1):
        title = doc.metadata.get("title", "Unknown")
        source = doc.metadata.get("source", "unknown")
        blocks.append(f"[{i}] {title} ({source})\n{doc.page_content}")
    return "\n\n---\n\n".join(blocks)


@lru_cache(maxsize=8)
def build_chain(k: int = 4, retriever_kind: str = "dense") -> Runnable:
    """Build the RAG LCEL chain. Cached per (k, retriever_kind).

    retriever_kind in {dense, sparse, hybrid}. Default is dense: the
    retriever comparison found dense best on this corpus (hybrid regressed
    Smaug + Dwarf-rings, and no retriever could fix Bombadil since the corpus
    lacks that article). hybrid/sparse remain available for A/B.
    Shape: question(str) -> {docs, question, context, answer}.
    The retriever runs once and the docs are carried through so the caller
    can build the source list without re-running retrieval.
    """
    retriever = get_retriever(k=k, kind=retriever_kind)
    llm = _build_llm()
    return (
        RunnableParallel(
            docs=retriever,
            question=RunnablePassthrough(),
        )
        | RunnablePassthrough.assign(
            context=lambda x: format_docs_for_prompt(x["docs"])
        )
        | RunnablePassthrough.assign(
            answer=(rag_prompt | llm | StrOutputParser())
        )
    )
