"""Coreference resolution: rewrite a follow-up question to be self-contained,
using prior conversation turns.

Multi-turn support. Without this, every retrieval on a follow-up like
"Who killed him?" runs with no entity and silently degrades. With it,
classification + retrieval + grading all see "Who killed Smaug?" instead.

Two design points worth keeping in mind:
  - The 'if self-contained, return verbatim' rule is load-bearing. Without it
    the LLM rewrites every question (even single-shot ones like "What is
    mithril?"), often introducing errors. Test on probes that don't need
    rewriting.
  - Empty-rewrite fallback: rare, but the LLM occasionally returns nothing on
    edge cases. Don't pass an empty string downstream.
"""
from langchain_anthropic import ChatAnthropic
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

coref_llm = ChatAnthropic(
    model="claude-sonnet-4-5", max_tokens=200, max_retries=5
)

COREF_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You rewrite follow-up questions to be self-contained, using prior "
            "conversation to resolve pronouns and references. Rules:\n"
            "- Resolve pronouns (he, she, it, they, him, her, this, that) "
            "using prior turns.\n"
            "- If the question is already self-contained, return it VERBATIM. "
            "Do not paraphrase or add implications.\n"
            "- Do not add information the user did not implicitly reference.\n"
            "- Output only the rewritten question. No preamble, no explanation.",
        ),
        (
            "human",
            "Conversation history:\n{history}\n\n"
            "Follow-up question: {question}\n\n"
            "Self-contained question:",
        ),
    ]
)


async def resolve_coreferences(question: str, history: list[dict]) -> str:
    """history is a list of {'role': 'user'|'assistant', 'content': str}."""
    if not history:
        return question
    history_text = "\n".join(
        f"{t['role'].title()}: {t['content']}" for t in history
    )
    chain = COREF_PROMPT | coref_llm | StrOutputParser()
    rewritten = (
        await chain.ainvoke({"history": history_text, "question": question})
    ).strip()
    return rewritten or question
