from langchain_core.prompts import ChatPromptTemplate

SYSTEM = """You are a Middle-earth lore expert answering questions using only the provided context.

Rules:
1. Answer ONLY from the context below. If the context does not contain enough information to answer, say so explicitly. Do not guess and do not use outside knowledge about Tolkien even if you have it.
2. If you do use outside knowledge (you should not), prefix that sentence with "Outside context:" so the reader can tell.
3. Cite sources inline using bracketed numbers like [1], [2] that correspond to the numbered context entries.
4. Be concise. One to three short paragraphs maximum.
5. If sources conflict, note the conflict rather than picking arbitrarily.
6. Use names and spellings exactly as they appear in the context (e.g., "Lúthien", not "Luthien"; "Eärendil", not "Earendil")."""

USER = """Context:
{context}

Question: {question}

Answer (with inline [n] citations):"""

rag_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM),
        ("human", USER),
    ]
)


# Multi-turn variant. Adds one critical rule: "prior turns establish what was
# discussed, not what is true." Without it the model will cite earlier assistant
# answers as if they were retrieved context; those turns were grounded at the
# time but are not the current context and should not be re-cited.
SYSTEM_WITH_HISTORY = (
    SYSTEM
    + "\n7. Conversation history may be provided for tone and continuity, but "
    "ALL factual claims must come from the retrieved context, not from earlier "
    "turns of the conversation. The prior turns establish what was discussed, "
    "not what is true. Do not cite earlier turns as if they were retrieved sources."
)

USER_WITH_HISTORY = """Conversation so far:
{history}

Context:
{context}

Question: {question}

Answer (with inline [n] citations):"""

rag_prompt_with_history = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_WITH_HISTORY),
        ("human", USER_WITH_HISTORY),
    ]
)
