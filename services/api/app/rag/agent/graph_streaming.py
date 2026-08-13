"""Async graph for the streaming path.

Runs coref -> classify -> retrieve -> grade -> [rewrite -> retrieve] and exits
once retrieval is settled. Synthesis is not a node: LangGraph nodes return
state, not async iterators, so the endpoint pipes the final state through
synthesize_streaming() and yields tokens as they arrive.

Shares prompts, LLMs, and the routing decision with graph.py so the streaming
and non-streaming paths cannot drift apart.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from langchain_core.output_parsers import PydanticOutputParser, StrOutputParser
from langgraph.graph import END, START, StateGraph

from app.rag.agent.coref import resolve_coreferences
from app.rag.agent.graph import (
    AgentState,
    Classification,
    GradeOutput,
    build_retriever,
    classifier_llm,
    classify_prompt,
    decide_next,
    grade_llm,
    grade_prompt,
    rag_prompt,
    rewrite_llm,
    rewrite_prompt,
    synth_llm,
)
from app.rag.chain.prompts import rag_prompt_with_history


def _active_question(state: AgentState) -> str:
    """Post-coref question when there is one, else the original."""
    return state.get("resolved_question") or state["question"]


# --- nodes ------------------------------------------------------------------


async def aresolve_query(state: AgentState) -> dict:
    """Resolve pronouns against prior turns. No-op without history."""
    history = state.get("history") or []
    if not history:
        return {"resolved_question": state["question"], "trace": ["no history"]}
    resolved = await resolve_coreferences(state["question"], history)
    return {
        "resolved_question": resolved,
        "trace": [f"resolved: {state['question']!r} -> {resolved!r}"],
    }


async def aclassify_query(state: AgentState) -> dict:
    parser = PydanticOutputParser(pydantic_object=Classification)
    chain = classify_prompt | classifier_llm | parser
    try:
        result = await chain.ainvoke({"question": _active_question(state)})
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


async def aretrieve(state: AgentState) -> dict:
    # Priority: rewrite output > coref-resolved > original.
    question = state.get("rewritten_question") or _active_question(state)
    retriever, kind = build_retriever(state)
    docs = await retriever.ainvoke(question)
    return {
        "documents": docs,
        "trace": [
            f"retrieved {len(docs)} docs via {kind} "
            f"(attempt {state.get('attempt', 0) + 1})"
        ],
    }


async def agrade_documents(state: AgentState) -> dict:
    parser = PydanticOutputParser(pydantic_object=GradeOutput)
    chain = grade_prompt | grade_llm | parser
    passages = "\n\n---\n\n".join(
        f"[{i}] {d.metadata.get('title', '?')}\n{d.page_content[:500]}"
        for i, d in enumerate(state["documents"], 1)
    )
    try:
        result = await chain.ainvoke(
            {"question": _active_question(state), "passages": passages}
        )
        return {"grade": result.grade, "trace": [f"graded {result.grade}: {result.reasoning}"]}
    except Exception as e:
        return {"grade": "partial", "trace": [f"grading failed ({e}); defaulted to partial"]}


async def arewrite_query(state: AgentState) -> dict:
    chain = rewrite_prompt | rewrite_llm | StrOutputParser()
    rewritten = (await chain.ainvoke({"question": _active_question(state)})).strip()
    return {
        "rewritten_question": rewritten,
        "attempt": state.get("attempt", 0) + 1,
        "trace": [f"rewrote to: {rewritten}"],
    }


# --- streaming synthesis (outside the graph) --------------------------------


async def synthesize_streaming(state: AgentState) -> AsyncIterator[dict]:
    """Yield token frames, then one answer_complete, or an error frame.

    Errors travel in-band because the HTTP 200 has already been sent.
    """
    context = "\n\n---\n\n".join(
        f"[{i}] {d.metadata.get('title', '?')}\n{d.page_content}"
        for i, d in enumerate(state.get("documents", []), 1)
    )
    history = state.get("history") or []
    question = _active_question(state)

    if history:
        chain = rag_prompt_with_history | synth_llm | StrOutputParser()
        inputs = {
            "context": context,
            "question": question,
            "history": "\n".join(
                f"{t['role'].title()}: {t['content']}" for t in history
            ),
        }
    else:
        chain = rag_prompt | synth_llm | StrOutputParser()
        inputs = {"context": context, "question": question}

    parts: list[str] = []
    try:
        async for chunk in chain.astream(inputs):
            parts.append(chunk)
            yield {"type": "token", "content": chunk}
    except Exception as e:
        yield {"type": "error", "message": str(e)}
        return
    yield {"type": "answer_complete", "content": "".join(parts)}


# --- graph ------------------------------------------------------------------


def build_streaming_agent_with_memory():
    g = StateGraph(AgentState)
    g.add_node("resolve_query", aresolve_query)
    g.add_node("classify_query", aclassify_query)
    g.add_node("retrieve", aretrieve)
    g.add_node("grade_documents", agrade_documents)
    g.add_node("rewrite_query", arewrite_query)

    g.add_edge(START, "resolve_query")
    g.add_edge("resolve_query", "classify_query")
    g.add_edge("classify_query", "retrieve")
    g.add_edge("retrieve", "grade_documents")
    # "synthesize" exits the graph; the endpoint streams it instead.
    g.add_conditional_edges(
        "grade_documents", decide_next, {"synthesize": END, "rewrite_query": "rewrite_query"}
    )
    g.add_edge("rewrite_query", "retrieve")
    return g.compile()


_agent = None


def get_streaming_agent_with_memory():
    global _agent
    if _agent is None:
        _agent = build_streaming_agent_with_memory()
    return _agent
