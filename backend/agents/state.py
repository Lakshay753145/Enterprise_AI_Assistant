"""LangGraph state.

One dict flows through every node. Nodes return partial updates which LangGraph
merges. `timings` and `trace` use custom reducers so each node can contribute
without clobbering what earlier nodes recorded.
"""

from __future__ import annotations

import operator
import time
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from sqlalchemy.ext.asyncio import AsyncSession

GateCategory = Literal["in_scope", "out_of_scope", "chitchat", "unsafe"]
Route = Literal["knowledge_base", "sql"]


def merge_timings(
    left: dict[str, float] | None, right: dict[str, float] | None
) -> dict[str, float]:
    """Accumulate per-node latency instead of overwriting it."""
    return {**(left or {}), **(right or {})}


class AgentState(TypedDict, total=False):
    # --- Request context (set once, read everywhere) ------------------------
    question: str
    department: str
    role: str
    username: str
    user_id: int
    conversation_id: str
    request_id: str
    #: Prior turns as [{"role": "user"|"assistant", "content": str}, ...],
    #: already truncated to CHAT_HISTORY_WINDOW by the caller.
    history: list[dict[str, str]]
    #: Live DB session, carried so retrieval nodes reuse the request's
    #: transaction - and therefore its RLS department context.
    session: AsyncSession

    # --- Relevance gate ------------------------------------------------------
    gate_category: GateCategory
    gate_reason: str
    gate_confidence: float

    # --- Query rewriting -----------------------------------------------------
    search_query: str
    query_variants: list[str]
    keywords: list[str]

    # --- Routing -------------------------------------------------------------
    route: Route
    route_reason: str

    # --- Retrieval -----------------------------------------------------------
    candidates: list[Any]  # list[RetrievedChunk] pre-rerank
    chunks: list[Any]  # list[RetrievedChunk] post-rerank
    top_score: float
    retrieval_stats: dict[str, Any]

    # --- Generation ----------------------------------------------------------
    messages: Annotated[list[BaseMessage], operator.add]
    answer: str
    citations: list[dict[str, Any]]
    answer_source: str
    confidence: float
    grounded: bool
    unsupported_claims: list[str]

    # --- Instrumentation -----------------------------------------------------
    timings: Annotated[dict[str, float], merge_timings]
    trace: Annotated[list[str], operator.add]
    error: str | None


class NodeTimer:
    """Context manager that records a node's wall-clock time into state.

    Usage::

        with NodeTimer("retrieve") as timer:
            ...
        return {"timings": timer.result, "trace": [timer.label]}
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self._started = 0.0
        self.elapsed_ms = 0.0

    def __enter__(self) -> "NodeTimer":
        self._started = time.perf_counter()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.elapsed_ms = round((time.perf_counter() - self._started) * 1000, 2)

    @property
    def result(self) -> dict[str, float]:
        return {self.label: self.elapsed_ms}


def initial_state(
    *,
    question: str,
    department: str,
    role: str,
    username: str,
    user_id: int,
    conversation_id: str,
    request_id: str,
    session: AsyncSession,
    history: list[dict[str, str]] | None = None,
) -> AgentState:
    return AgentState(
        question=question.strip(),
        department=department,
        role=role,
        username=username,
        user_id=user_id,
        conversation_id=conversation_id,
        request_id=request_id,
        session=session,
        history=history or [],
        candidates=[],
        chunks=[],
        citations=[],
        query_variants=[],
        keywords=[],
        messages=[],
        timings={},
        trace=[],
        top_score=0.0,
        confidence=0.0,
        grounded=True,
        unsupported_claims=[],
        error=None,
    )
