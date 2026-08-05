r"""LangGraph assembly.

    START
      |
    gate ------------------> chitchat ------------> END
      |  \---------------> refuse_out_of_scope ---> END
      |  \---------------> refuse_unsafe --------> END
      v
    rewrite
      |
    route ---(sql)--------> sql_agent -----------> END
      |
      v (knowledge_base)
    retrieve
      |
    rerank
      |  \---------------> refuse_no_evidence ---> END
      v (evidence above confidence threshold)
    generate
      |
    verify -------------------------------------> END

Compiled once at import and reused; it is stateless between invocations, so a
single instance is safe under concurrency.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from backend.agents.nodes import (
    chitchat_node,
    evidence_branch,
    gate_branch,
    gate_node,
    generate_node,
    refuse_no_evidence_node,
    refuse_out_of_scope_node,
    refuse_unsafe_node,
    rerank_node,
    retrieve_node,
    rewrite_node,
    route_branch,
    route_node,
    verify_node,
)
from backend.agents.sql_agent import sql_agent_node
from backend.agents.state import AgentState
from backend.core.logging_config import app_logger


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("gate", gate_node)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("route", route_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("rerank", rerank_node)
    graph.add_node("generate", generate_node)
    graph.add_node("verify", verify_node)
    graph.add_node("sql_agent", sql_agent_node)
    graph.add_node("chitchat", chitchat_node)
    graph.add_node("refuse_out_of_scope", refuse_out_of_scope_node)
    graph.add_node("refuse_unsafe", refuse_unsafe_node)
    graph.add_node("refuse_no_evidence", refuse_no_evidence_node)

    graph.add_edge(START, "gate")

    graph.add_conditional_edges(
        "gate",
        gate_branch,
        {
            "rewrite": "rewrite",
            "chitchat": "chitchat",
            "refuse_out_of_scope": "refuse_out_of_scope",
            "refuse_unsafe": "refuse_unsafe",
        },
    )

    graph.add_edge("rewrite", "route")

    graph.add_conditional_edges(
        "route",
        route_branch,
        {"retrieve": "retrieve", "sql_agent": "sql_agent"},
    )

    graph.add_edge("retrieve", "rerank")

    graph.add_conditional_edges(
        "rerank",
        evidence_branch,
        {"generate": "generate", "refuse_no_evidence": "refuse_no_evidence"},
    )

    graph.add_edge("generate", "verify")

    for terminal in (
        "verify",
        "sql_agent",
        "chitchat",
        "refuse_out_of_scope",
        "refuse_unsafe",
        "refuse_no_evidence",
    ):
        graph.add_edge(terminal, END)

    return graph


@lru_cache(maxsize=1)
def get_compiled_graph():
    compiled = build_graph().compile()
    app_logger.info("LangGraph pipeline compiled")
    return compiled


def render_mermaid() -> str:
    """Mermaid source for the graph - surfaced on the admin diagnostics page."""
    try:
        return get_compiled_graph().get_graph().draw_mermaid()
    except Exception as exc:  # pragma: no cover
        app_logger.warning(f"Could not render graph diagram: {exc}")
        return ""
