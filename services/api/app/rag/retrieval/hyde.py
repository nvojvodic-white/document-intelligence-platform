"""HyDE retriever: generate a hypothetical answer, embed it, retrieve on it.

HyDE targets the low-lexical-overlap failure mode (the Smaug->Bard case): the
question and its answer live in different parts of embedding space, so embedding
a plausible-shaped hypothetical answer lands closer to the right chunks than
embedding the raw question. The hypothetical need not be factually correct -
dense retrieval grounds the actual facts; the hypothetical just provides
answer-shaped semantic content to match against.
"""
from functools import lru_cache

from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import (
    AsyncCallbackManagerForRetrieverRun,
    CallbackManagerForRetrieverRun,
)
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever

from app.rag.retrieval.vectorstore import get_vectorstore

HYDE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a Middle-earth lore expert. Write a concise, "
            "plausible-sounding encyclopedia-style passage that answers the "
            "user's question. Aim for 2-4 sentences of dense factual content. "
            "The passage will be used for similarity search - it does not need "
            "to be perfectly accurate, but it should read like a real "
            "encyclopedia entry from a Tolkien reference work. Write "
            "confidently; do not hedge.",
        ),
        ("human", "Question: {question}\n\nHypothetical passage:"),
    ]
)


@lru_cache(maxsize=1)
def _hyde_chain():
    llm = ChatAnthropic(model="claude-sonnet-4-5", max_tokens=400, max_retries=5)
    return HYDE_PROMPT | llm | StrOutputParser()


class HyDERetriever(BaseRetriever):
    """Retrieve via the embedding of a hypothetical answer, not the raw query."""

    k: int = 4

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        hypothetical = _hyde_chain().invoke({"question": query})
        return get_vectorstore().similarity_search(hypothetical, k=self.k)

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun
    ) -> list[Document]:
        # Streaming-path version: never block the event loop on either the
        # hypothetical-generation LLM call or the Chroma query.
        hypothetical = await _hyde_chain().ainvoke({"question": query})
        return await get_vectorstore().asimilarity_search(hypothetical, k=self.k)


def get_hyde_retriever(k: int = 4) -> HyDERetriever:
    return HyDERetriever(k=k)
