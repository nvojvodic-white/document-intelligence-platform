"""HyDE: generate a hypothetical answer, retrieve on its embedding.

HyDE targets the low-lexical-overlap failure mode (the Smaug->Bard case): the
question and its answer live in different parts of embedding space, so
embedding a plausible answer-shaped passage lands closer to the right chunks
than embedding the raw question. The hypothetical need not be factually
correct - retrieval grounds the actual facts, the hypothetical only supplies
answer-shaped semantic content to match against.

Rewritten for the fork to search one user's collection. The hypothetical is
generated from the question alone and is never grounded in anyone's documents,
so generating it leaks nothing; only the search it drives is scoped, and that
scoping is the same user-scoped retrieve() every other kind uses.
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

from app.rag.retrieval.user_scoped import retrieve

HYDE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Write a concise, plausible-sounding encyclopedia-style passage "
            "that answers the user's question. Aim for 2-4 sentences of dense "
            "factual content. The passage is used for similarity search - it "
            "does not need to be perfectly accurate, but it should read like a "
            "real reference entry. Write confidently; do not hedge.",
        ),
        ("human", "Question: {question}\n\nHypothetical passage:"),
    ]
)


@lru_cache(maxsize=1)
def _hyde_chain():
    llm = ChatAnthropic(model="claude-sonnet-4-5", max_tokens=400, max_retries=5)
    return HYDE_PROMPT | llm | StrOutputParser()


class HyDERetriever(BaseRetriever):
    """Retrieve on the embedding of a hypothetical answer, not the raw query."""

    user_id: str
    k: int = 4

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        hypothetical = _hyde_chain().invoke({"question": query})
        return retrieve(self.user_id, hypothetical, k=self.k, kind="dense")

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun
    ) -> list[Document]:
        # Never block the event loop on the hypothetical-generation LLM call.
        hypothetical = await _hyde_chain().ainvoke({"question": query})
        return retrieve(self.user_id, hypothetical, k=self.k, kind="dense")


def get_hyde_retriever(user_id: str, k: int = 4) -> HyDERetriever:
    return HyDERetriever(user_id=user_id, k=k)
