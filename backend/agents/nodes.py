"""LangGraph nodes.

Pipeline, in order:

    gate -> rewrite -> route -> retrieve -> rerank -> generate -> verify

with early exits to refusal nodes at the gate and after reranking. Each node is
a plain async function taking state and returning a partial update, which keeps
them individually testable without standing up the graph.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from backend.config.config import settings
from backend.core.constants import (
    REFUSAL_NO_EVIDENCE,
    REFUSAL_OUT_OF_SCOPE,
    REFUSAL_UNSAFE,
    AnswerSource,
)
from backend.core.logging_config import app_logger, write_audit_event
from backend.llm.ollama_client import astructured, get_llm
from backend.prompts.templates import (
    ANSWER_USER,
    GROUNDING_CHECK_SYSTEM,
    GROUNDING_CHECK_USER,
    build_answer_system,
    build_chitchat_system,
    build_relevance_gate_system,
    build_rewrite_system,
    build_router_system,
    format_context,
)
from backend.retrieval.hybrid_search import hybrid_search
from backend.retrieval.reranker import rerank
from backend.agents.state import AgentState, NodeTimer

# Tokens carrying this tag are the ones the SSE layer forwards to the browser.
# Everything else the graph asks a model to do (classification, rewriting,
# fact-checking) stays internal.
FINAL_ANSWER_TAG = "final_answer"


# ===========================================================================
# Structured outputs
# ===========================================================================

class GateVerdict(BaseModel):
    category: str = "in_scope"
    confidence: float = 0.5
    reason: str = ""


class RewriteOutput(BaseModel):
    search_query: str = ""
    variants: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class RouteOutput(BaseModel):
    destination: str = "knowledge_base"
    reason: str = ""


class GroundingVerdict(BaseModel):
    grounded: bool = True
    unsupported_claims: list[str] = Field(default_factory=list)
    confidence: float = 0.5


# ===========================================================================
# 1. Relevance gate
# ===========================================================================

async def gate_node(state: AgentState) -> dict[str, Any]:
    """Classify the question before spending any retrieval or generation.

    Runs on the small model. Fails *open* (in_scope) on error: a broken
    classifier should not block legitimate work, and the grounding check
    downstream still prevents an ungrounded answer from reaching the user.
    """
    with NodeTimer("gate_ms") as timer:
        if not settings.RELEVANCE_GATE_ENABLED:
            return {
                "gate_category": "in_scope",
                "gate_reason": "gate disabled",
                "timings": {"gate_ms": 0.0},
                "trace": ["gate:skipped"],
            }

        messages = [
            SystemMessage(content=build_relevance_gate_system(state["department"])),
            *_history_messages(state, limit=4),
            HumanMessage(content=state["question"]),
        ]

        print("\n=========== GATE SYSTEM PROMPT ===========")
        print(build_relevance_gate_system(state["department"]))
        print("=========================================\n")

        verdict = await astructured(
            messages,
            GateVerdict,
            fast=True,
            default=GateVerdict(
                category="in_scope", confidence=0.0, reason="classifier unavailable"
            ),
        )
        print("\n================ GATE DEBUG ================")
        print("QUESTION :", state["question"])
        print("CATEGORY :", verdict.category)
        print("CONFIDENCE :", verdict.confidence)
        print("REASON :", verdict.reason)
        print("===========================================\n")

    category = verdict.category if verdict.category in {
        "in_scope",
        "out_of_scope",
        "chitchat",
        "unsafe",
    } else "in_scope"

    if category == "unsafe":
        write_audit_event(
            "chat_unsafe_query_blocked",
            username=state.get("username"),
            user_id=state.get("user_id"),
            department=state.get("department"),
            request_id=state.get("request_id"),
            success=False,
            detail={"question": state["question"][:500], "reason": verdict.reason},
        )

    app_logger.debug(
        f"Gate [{state['department']}] -> {category} ({verdict.confidence:.2f}): "
        f"{verdict.reason}"
    )

    return {
        "gate_category": category,
        "gate_reason": verdict.reason,
        "gate_confidence": verdict.confidence,
        "timings": timer.result,
        "trace": [f"gate:{category}"],
    }


# ===========================================================================
# 2. Query rewriter
# ===========================================================================

async def rewrite_node(state: AgentState) -> dict[str, Any]:
    """Turn shop-floor phrasing into the documentation's vocabulary.

    Falls back to the raw question on failure - a worse query is still a
    query, whereas a hard failure here would kill an answerable question.
    """
    with NodeTimer("rewrite_ms") as timer:
        question = state["question"]

        messages = [
            SystemMessage(content=build_rewrite_system(state["department"])),
            *_history_messages(state, limit=4),
            HumanMessage(content=f"Rewrite this question: {question}"),
        ]

        result = await astructured(
            messages,
            RewriteOutput,
            fast=True,
            default=RewriteOutput(search_query=question),
        )

    search_query = (result.search_query or "").strip() or question

    # Guard against a small model that "helpfully" answers instead of
    # rewriting, or returns something unrelated to the question.
    if len(search_query) > 500:
        search_query = question

    variants = [v.strip() for v in (result.variants or []) if v and v.strip()][:2]

    app_logger.debug(f"Rewrite: {question[:60]!r} -> {search_query[:80]!r}")

    return {
        "search_query": search_query,
        "query_variants": variants,
        "keywords": [k.strip() for k in (result.keywords or []) if k.strip()][:8],
        "timings": timer.result,
        "trace": ["rewrite"],
    }


# ===========================================================================
# 3. Router
# ===========================================================================

async def route_node(state: AgentState) -> dict[str, Any]:
    """Knowledge base (default) vs SQL agent (questions *about* the corpus)."""
    with NodeTimer("route_ms") as timer:
        if not settings.SQL_AGENT_ENABLED:
            return {
                "route": "knowledge_base",
                "route_reason": "sql agent disabled",
                "timings": {"route_ms": 0.0},
                "trace": ["route:knowledge_base"],
            }

        messages = [
            SystemMessage(content=build_router_system(state["department"])),
            HumanMessage(content=state["question"]),
        ]
        result = await astructured(
            messages,
            RouteOutput,
            fast=True,
            default=RouteOutput(destination="knowledge_base", reason="router default"),
        )

    destination = "sql" if result.destination == "sql" else "knowledge_base"
    return {
        "route": destination,
        "route_reason": result.reason,
        "timings": timer.result,
        "trace": [f"route:{destination}"],
    }


# ===========================================================================
# 4. Retrieval
# ===========================================================================

async def retrieve_node(state: AgentState) -> dict[str, Any]:
    with NodeTimer("retrieve_ms") as timer:
        result = await hybrid_search(
            state["session"],
            query=state.get("search_query") or state["question"],
            department=state["department"],
            role=state["role"],
            username=state.get("username"),
            query_variants=state.get("query_variants") or [],
        )

    return {
        "candidates": result.chunks,
        "retrieval_stats": {
            "vector_hits": result.vector_hits,
            "keyword_hits": result.keyword_hits,
            "fused_hits": result.fused_hits,
            **result.timings_ms,
        },
        "timings": timer.result,
        "trace": [f"retrieve:{len(result.chunks)}"],
    }


# ===========================================================================
# 5. Reranking
# ===========================================================================

async def rerank_node(state: AgentState) -> dict[str, Any]:
    with NodeTimer("rerank_ms") as timer:
        result = await rerank(
            state.get("search_query") or state["question"],
            list(state.get("candidates") or []),
        )

    return {
        "chunks": result.chunks,
        "top_score": result.top_score,
        "confidence": result.top_score,
        "timings": timer.result,
        "trace": [f"rerank:{result.kept}/{result.considered}@{result.top_score:.2f}"],
    }


# ===========================================================================
# 6. Generation
# ===========================================================================

import traceback


async def generate_node(state: AgentState) -> dict[str, Any]:
    try:
        with NodeTimer("generate_ms") as timer:
            chunks = state.get("chunks") or []
            context = format_context(chunks)

            messages = [
                SystemMessage(content=build_answer_system(state["department"])),
                *_history_messages(
                    state,
                    limit=settings.CHAT_HISTORY_WINDOW,
                ),
                HumanMessage(
                    content=ANSWER_USER.format(
                        context=context,
                        question=state["question"],
                    )
                ),
            ]

            llm = get_llm().with_config(
                tags=[FINAL_ANSWER_TAG],
                run_name="final_answer",
            )

            answer = await _collect_stream(llm, messages)

        citations = _citations_actually_used(answer, chunks)

        return {
            "answer": answer,
            "citations": citations,
            "answer_source": AnswerSource.KNOWLEDGE_BASE.value,
            "messages": [AIMessage(content=answer)],
            "timings": timer.result,
            "trace": [f"generate:{len(answer)}chars"],
        }

    except Exception:
        traceback.print_exc()
        raise
# ===========================================================================
# 7. Grounding verification
# ===========================================================================

async def verify_node(state: AgentState) -> dict[str, Any]:
    """Second-pass fact check.

    Does not rewrite or block the answer - by this point the user has already
    seen it stream. What it does is record an honest verdict alongside the
    message, so a low-faithfulness answer is visible in the UI and queryable in
    the audit trail rather than silently trusted.
    """
    if not settings.GROUNDING_CHECK_ENABLED:
        return {"grounded": True, "trace": ["verify:skipped"]}

    answer = state.get("answer") or ""
    chunks = state.get("chunks") or []

    if not answer or not chunks or _is_refusal(answer):
        return {"grounded": True, "trace": ["verify:na"]}

    with NodeTimer("verify_ms") as timer:
        verdict = await astructured(
            [
                SystemMessage(content=GROUNDING_CHECK_SYSTEM),
                HumanMessage(
                    content=GROUNDING_CHECK_USER.format(
                        context=format_context(chunks), answer=answer
                    )
                ),
            ],
            GroundingVerdict,
            fast=True,
            default=GroundingVerdict(grounded=True, confidence=0.0),
        )
        print("\n========== GROUNDING ==========")
        print("Grounded:", verdict.grounded)
        print("Confidence:", verdict.confidence)
        print("Unsupported:", verdict.unsupported_claims)
        print("===============================\n")

    if not verdict.grounded:
        app_logger.warning(
            f"Ungrounded answer for {state.get('username')} "
            f"[{state.get('department')}]: {verdict.unsupported_claims}"
        )
        write_audit_event(
            "chat_answer_ungrounded",
            username=state.get("username"),
            user_id=state.get("user_id"),
            department=state.get("department"),
            request_id=state.get("request_id"),
            success=False,
            detail={
                "question": state["question"][:300],
                "unsupported_claims": verdict.unsupported_claims[:5],
            },
        )

    return {
        "grounded": verdict.grounded,
        "unsupported_claims": verdict.unsupported_claims,
        "timings": timer.result,
        "trace": [f"verify:{'ok' if verdict.grounded else 'ungrounded'}"],
    }


# ===========================================================================
# 8. Terminal nodes
# ===========================================================================

async def chitchat_node(state: AgentState) -> dict[str, Any]:
    with NodeTimer("generate_ms") as timer:
        llm = get_llm().with_config(tags=[FINAL_ANSWER_TAG], run_name="final_answer")
        answer = await _collect_stream(
            llm,
            [
                SystemMessage(content=build_chitchat_system(state["department"])),
                HumanMessage(content=state["question"]),
            ],
        )

    return {
        "answer": answer,
        "citations": [],
        "answer_source": AnswerSource.KNOWLEDGE_BASE.value,
        "confidence": 1.0,
        "timings": timer.result,
        "trace": ["chitchat"],
    }


async def refuse_out_of_scope_node(state: AgentState) -> dict[str, Any]:
    return {
        "answer": REFUSAL_OUT_OF_SCOPE.format(department=state["department"]),
        "citations": [],
        "answer_source": AnswerSource.REFUSED_OUT_OF_SCOPE.value,
        "confidence": 1.0,
        "trace": ["refuse:out_of_scope"],
    }


async def refuse_unsafe_node(state: AgentState) -> dict[str, Any]:
    return {
        "answer": REFUSAL_UNSAFE,
        "citations": [],
        "answer_source": AnswerSource.REFUSED_OUT_OF_SCOPE.value,
        "confidence": 1.0,
        "trace": ["refuse:unsafe"],
    }


async def refuse_no_evidence_node(state: AgentState) -> dict[str, Any]:
    """Nothing in the knowledge base cleared the confidence bar.

    Answering anyway is how a RAG system produces its most damaging output: a
    confident, well-formatted, entirely invented figure. Refusing is correct.
    """
    app_logger.info(
        f"No evidence for {state.get('username')} [{state['department']}]: "
        f"{state['question'][:80]!r} (top score "
        f"{state.get('top_score', 0.0):.3f})"
    )
    return {
        "answer": REFUSAL_NO_EVIDENCE.format(department=state["department"]),
        "citations": [],
        "answer_source": AnswerSource.REFUSED_NO_EVIDENCE.value,
        "confidence": state.get("top_score", 0.0),
        "trace": ["refuse:no_evidence"],
    }


# ===========================================================================
# Conditional edges
# ===========================================================================

def gate_branch(state: AgentState) -> str:
    return {
        "in_scope": "rewrite",
        "chitchat": "chitchat",
        "out_of_scope": "refuse_out_of_scope",
        "unsafe": "refuse_unsafe",
    }.get(state.get("gate_category", "in_scope"), "rewrite")


def route_branch(state: AgentState) -> str:
    return "sql_agent" if state.get("route") == "sql" else "retrieve"


def evidence_branch(state: AgentState) -> str:
    """Generate only when the evidence clears the confidence bar."""
    chunks = state.get("chunks") or []
    if not chunks:
        return "refuse_no_evidence"
    if state.get("top_score", 0.0) < settings.MIN_CONFIDENCE_THRESHOLD:
        return "refuse_no_evidence"
    return "generate"


# ===========================================================================
# Helpers
# ===========================================================================

async def _collect_stream(llm, messages: list) -> str:
    """Stream a completion and return the assembled text.

    The streaming is the point: it is what produces the on_chat_model_stream
    events the SSE layer forwards to the browser. The assembled string is what
    gets persisted and fact-checked.
    """
    parts: list[str] = []
    async for chunk in llm.astream(messages):
        content = getattr(chunk, "content", "")
        if isinstance(content, str) and content:
            parts.append(content)
    return "".join(parts).strip()


def _history_messages(state: AgentState, *, limit: int) -> list:
    """Prior turns as LangChain messages, oldest first."""
    history = state.get("history") or []
    messages = []
    for turn in history[-limit:]:
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if turn.get("role") == "assistant":
            messages.append(AIMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    return messages


def _citations_actually_used(answer: str, chunks: list) -> list[dict[str, Any]]:
    """Return citations for the passages the answer actually cites.

    Listing every retrieved chunk as a "source" is the standard RAG lie: it
    implies the answer rests on passages it never used. We parse the [n]
    markers the model emitted and return only those.
    """
    import re

    if not chunks:
        return []

    referenced = {
        int(n)
        for n in re.findall(r"\[(\d{1,2})\]", answer)
        if 1 <= int(n) <= len(chunks)
    }

    # No markers (model ignored the instruction, or the answer is a refusal):
    # fall back to the top passage so the user still has something to verify
    # against, rather than an unattributed claim.
    if not referenced:
        if _is_refusal(answer):
            return []
        referenced = {1}

    citations = []
    for position in sorted(referenced):
        citation = chunks[position - 1].to_citation()
        citation["marker"] = position
        citations.append(citation)
    return citations


def _is_refusal(answer: str) -> bool:
    lowered = answer.lower()
    return (
        "could not find this information" in lowered
        or "outside the" in lowered and "knowledge base" in lowered
    )
