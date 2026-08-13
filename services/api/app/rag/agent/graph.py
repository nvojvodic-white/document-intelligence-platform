"""Routing RAG agent, non-streaming path.

Routes each question to a retriever rather than using one for everything:

  definitional -> hybrid  (BM25 rescues rare entity names)
  multi_hop    -> hyde    (question and answer sit apart in embedding space)
  general      -> dense

  START -> classify -> retrieve -> grade -> decide
                          ^                   |
                          +-- rewrite <- [poor & first attempt]
                                              v
                                        synthesize -> END

The streaming path in graph_streaming.py reuses these prompts, LLMs, and
decide_next so the two cannot drift apart.
"""
import time
from functools import wraps
from operator import add
from typing import Annotated, Literal, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.documents import Document
from langchain_core.output_parsers import PydanticOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.rag.chain.prompts import rag_prompt
from app.rag.retrieval.user_scoped import get_user_retriever


def timed(name: str):
    """Append a [timing] line to a node's trace delta.

    trace uses the `add` reducer, so a node returns only its own lines, never
    the running trace.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(state):
            t0 = time.perf_counter()
            result = fn(state)
            dt_ms = (time.perf_counter() - t0) * 1000
            node_trace = list(result.get("trace", []))
            node_trace.append(f"[timing] {name}: {dt_ms:.0f}ms")
            return {**result, "trace": node_trace}

        return wrapper

    return decorator

Route = Literal["definitional", "multi_hop", "general"]
Grade = Literal["relevant", "partial", "poor"]


class AgentState(TypedDict, total=False):
    # The verified caller. Set by the endpoint from the token and read by the
    # retrieve node; there is no default and no fallback, so a graph invoked
    # without it fails loudly rather than searching something arbitrary.
    user_id: str
    question: str
    # Post-coreference rewrite of `question` for multi-turn. Set by
    # resolve_query in graph_streaming. Downstream nodes read
    # state.get("resolved_question") or state["question"], so paths without
    # memory keep working byte-identically.
    resolved_question: str
    # Optional list of {"role": "user"|"assistant", "content": str}
    # carried into synthesis as conversation context.
    history: list[dict]
    # Opaque caller-provided session id. Only used to scope memory
    # reads/writes in the endpoint; the graph itself does not read it.
    session_id: str | None
    route: Route
    rewritten_question: str | None
    documents: list[Document]
    attempt: int
    grade: Grade
    answer: str
    trace: Annotated[list[str], add]


# Routing table. The pre-fork build measured definitional -> semantic, but the
# semantic index cannot be rebuilt per-user without a second embedding pass, so
# definitional now takes hybrid: BM25 over the user's own chunk rows rescues the
# rare-entity matches ("mithril", "Bombadil") that motivated semantic there.
ROUTE_TO_RETRIEVER: dict[Route, str] = {
    "definitional": "hybrid",
    "multi_hop": "hyde",
    "general": "dense",
}


# --- classify ---------------------------------------------------------------

classifier_llm = ChatAnthropic(
    model="claude-haiku-4-5", max_tokens=200, max_retries=5
)


class Classification(BaseModel):
    route: Route = Field(description="One of: definitional, multi_hop, general")
    reasoning: str = Field(description="One sentence explaining the choice")


classify_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Classify the user's question about Middle-earth into exactly one "
            "retrieval route:\n\n"
            "- `definitional`: asks what/who something is, requesting a concise "
            "description of a single entity, place, object, or concept. "
            "Examples: 'What is mithril?', 'Who is Tom Bombadil?'\n"
            "- `multi_hop`: the answer requires connecting entities or facts not "
            "present in the question. Examples: 'Who killed Smaug?' (answer is "
            "Bard, not in question), 'What rings did the Dwarves get?'\n"
            "- `general`: everything else - narratives, events, comparisons, "
            "broad surveys. Examples: 'Tell me about the Battle of Five Armies', "
            "'Describe Aragorn's role in the War'\n\n"
            "Reply with JSON: {{\"route\": <one of above>, \"reasoning\": <one sentence>}}",
        ),
        ("human", "{question}"),
    ]
)


@timed("classify")
def classify_query(state: AgentState) -> dict:
    parser = PydanticOutputParser(pydantic_object=Classification)
    chain = classify_prompt | classifier_llm | parser
    try:
        result = chain.invoke({"question": state["question"]})
        return {
            "route": result.route,
            "attempt": 0,
            "trace": [f"classified as {result.route}: {result.reasoning}"],
        }
    except Exception as e:
        return {
            "route": "general",
            "attempt": 0,
            "trace": [f"classification failed ({e}); defaulting to general"],
        }


# --- retrieve ---------------------------------------------------------------

# The pre-fork build cached retrieval on (question, kind, k). That cache cannot
# survive multi-tenancy: the key has no user in it, so the second user to ask a
# question would be served the first user's documents. Removed rather than
# re-keyed - per-user hit rates on a demo corpus would not pay for the risk.
def build_retriever(state: AgentState):
    """Build the retriever for this state's route, bound to this state's user."""
    user_id = state.get("user_id")
    if not user_id:
        raise ValueError("retrieval requires a user_id in the graph state")
    kind = ROUTE_TO_RETRIEVER[state["route"]]
    if kind == "hyde":
        from app.rag.retrieval.hyde import get_hyde_retriever

        return get_hyde_retriever(user_id, k=4), kind
    return get_user_retriever(user_id, k=4, kind=kind), kind


@timed("retrieve")
def retrieve(state: AgentState) -> dict:
    question = state.get("rewritten_question") or state["question"]
    retriever, kind = build_retriever(state)
    docs = list(retriever.invoke(question))
    return {
        "documents": docs,
        "trace": [
            f"retrieved {len(docs)} docs via {kind} "
            f"(attempt {state.get('attempt', 0) + 1})"
        ],
    }


# --- grade ------------------------------------------------------------------

grade_llm = ChatAnthropic(
    model="claude-haiku-4-5", max_tokens=300, max_retries=5
)


class GradeOutput(BaseModel):
    grade: Grade
    reasoning: str


grade_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are evaluating whether retrieved passages can answer a question. "
            "Grade the retrieval:\n\n"
            "- `relevant`: passages directly contain the information needed to answer.\n"
            "- `partial`: passages touch on the topic but lack key facts; an answer "
            "would be incomplete.\n"
            "- `poor`: passages are off-topic or irrelevant; the question cannot be "
            "answered from them.\n\n"
            "Be honest. A confident `poor` grade is more useful than a hopeful "
            "`relevant`.\n\n"
            "Reply with JSON: {{\"grade\": <one of above>, \"reasoning\": <one sentence>}}",
        ),
        (
            "human",
            "Question: {question}\n\nRetrieved passages:\n{passages}\n\nGrade:",
        ),
    ]
)


@timed("grade")
def grade_documents(state: AgentState) -> dict:
    parser = PydanticOutputParser(pydantic_object=GradeOutput)
    chain = grade_prompt | grade_llm | parser
    passages = "\n\n---\n\n".join(
        f"[{i}] {d.metadata.get('title', '?')}\n{d.page_content[:500]}"
        for i, d in enumerate(state["documents"], 1)
    )
    try:
        result = chain.invoke(
            {"question": state["question"], "passages": passages}
        )
        return {
            "grade": result.grade,
            "trace": [f"graded {result.grade}: {result.reasoning}"],
        }
    except Exception as e:
        return {
            "grade": "partial",
            "trace": [f"grading failed ({e}); defaulted to partial"],
        }


# --- rewrite ----------------------------------------------------------------

rewrite_llm = ChatAnthropic(
    model="claude-sonnet-4-5", max_tokens=200, max_retries=5
)

rewrite_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "The user asked a question and our first retrieval attempt returned "
            "poor passages. Rewrite the question to improve retrieval - be more "
            "specific, use synonyms for rare terms, add likely context words. "
            "Output only the rewritten question, no preamble.",
        ),
        ("human", "Original question: {question}\n\nRewritten question:"),
    ]
)


@timed("rewrite")
def rewrite_query(state: AgentState) -> dict:
    chain = rewrite_prompt | rewrite_llm | StrOutputParser()
    rewritten = chain.invoke({"question": state["question"]}).strip()
    return {
        "rewritten_question": rewritten,
        "attempt": state.get("attempt", 0) + 1,
        "trace": [f"rewrote to: {rewritten}"],
    }


# --- synthesize -------------------------------------------------------------

synth_llm = ChatAnthropic(
    model="claude-sonnet-4-5", max_tokens=1024, max_retries=5
)


@timed("synthesize")
def synthesize(state: AgentState) -> dict:
    context = "\n\n---\n\n".join(
        f"[{i}] {d.metadata.get('title', '?')}\n{d.page_content}"
        for i, d in enumerate(state["documents"], 1)
    )
    chain = rag_prompt | synth_llm | StrOutputParser()
    answer = chain.invoke({"context": context, "question": state["question"]})
    return {
        "answer": answer,
        "trace": [f"synthesized answer ({len(answer)} chars)"],
    }


# --- conditional edge -------------------------------------------------------

def decide_next(state: AgentState) -> str:
    grade = state.get("grade", "partial")
    attempt = state.get("attempt", 0)
    if grade == "relevant":
        return "synthesize"
    if grade == "poor" and attempt < 1:
        return "rewrite_query"
    return "synthesize"


# --- compile ----------------------------------------------------------------

def build_agent():
    g = StateGraph(AgentState)
    g.add_node("classify_query", classify_query)
    g.add_node("retrieve", retrieve)
    g.add_node("grade_documents", grade_documents)
    g.add_node("rewrite_query", rewrite_query)
    g.add_node("synthesize", synthesize)

    g.add_edge(START, "classify_query")
    g.add_edge("classify_query", "retrieve")
    g.add_edge("retrieve", "grade_documents")
    g.add_conditional_edges(
        "grade_documents",
        decide_next,
        {"synthesize": "synthesize", "rewrite_query": "rewrite_query"},
    )
    g.add_edge("rewrite_query", "retrieve")
    g.add_edge("synthesize", END)
    return g.compile()


_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent
