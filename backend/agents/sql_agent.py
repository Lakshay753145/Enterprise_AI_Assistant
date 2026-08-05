"""LangChain SQL agent, scoped to one department.

Answers questions *about* the corpus ("how many documents do we have?",
"which documents mention heat treatment?") rather than questions answered from
*inside* documents.

Letting an LLM write SQL against a production database is only acceptable
because of what it cannot reach. Four independent constraints:

1. The connection uses the ``ai_readonly`` Postgres role, which holds no
   privileges on any base table.
2. That role has ``default_transaction_read_only = on`` set at role level and
   again in the connection string, so no statement can mutate anything.
3. ``SQLDatabase`` is constructed with ``include_tables`` naming only the two
   views for the caller's department, so the agent's schema prompt cannot even
   describe another department's data.
4. Each view hard-codes ``WHERE department = '<Dept>'`` with
   ``security_barrier``, so the department predicate is inside the view, not
   in agent-generated SQL that could be talked out of it.

A perfect prompt injection therefore yields, at worst, data the signed-in user
was already entitled to read.
"""

from __future__ import annotations

import asyncio
import re
from functools import lru_cache
from typing import Any

from backend.config.config import settings
from backend.core.constants import AnswerSource, Department
from backend.core.exceptions import ValidationError
from backend.core.logging_config import app_logger, write_audit_event
from backend.database.database import get_readonly_engine
from backend.llm.ollama_client import get_llm
from backend.prompts.templates import build_sql_agent_prefix
from backend.agents.state import AgentState, NodeTimer

#: Statements that must never appear in generated SQL. The read-only role
#: already rejects them; this catches them earlier and, more importantly,
#: records the attempt.
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|"
    r"pg_read_file|pg_ls_dir|dblink|pg_sleep)\b",
    re.IGNORECASE,
)


def department_views(department: str) -> list[str]:
    if not Department.is_valid(department):
        raise ValidationError(f"Unknown department: {department}")
    slug = department.lower()
    return [f"kb_{slug}_documents", f"kb_{slug}_chunks"]


@lru_cache(maxsize=len(Department.values()))
def _build_agent(department: str):
    """Build (and cache) the agent executor for a department."""
    from langchain_community.agent_toolkits.sql.base import create_sql_agent
    from langchain_community.agent_toolkits.sql.toolkit import SQLDatabaseToolkit
    from langchain_community.utilities import SQLDatabase

    views = department_views(department)

    database = SQLDatabase(
        engine=get_readonly_engine(),
        include_tables=views,
        view_support=True,
        sample_rows_in_table_info=2,
        max_string_length=500,
    )

    llm = get_llm()
    toolkit = SQLDatabaseToolkit(db=database, llm=llm)

    return create_sql_agent(
        llm=llm,
        toolkit=toolkit,
        agent_type="zero-shot-react-description",
        prefix=build_sql_agent_prefix(
            department, views, settings.SQL_AGENT_MAX_ROWS
        ),
        verbose=settings.DEBUG,
        max_iterations=6,
        max_execution_time=45,
        early_stopping_method="force",
        handle_parsing_errors=True,
        top_k=settings.SQL_AGENT_MAX_ROWS,
    )


def _run_sync(department: str, question: str) -> dict[str, Any]:
    agent = _build_agent(department)
    return agent.invoke({"input": question})


async def sql_agent_node(state: AgentState) -> dict[str, Any]:
    """Graph node wrapping the SQL agent."""
    department = state["department"]
    question = state["question"]

    with NodeTimer("sql_agent_ms") as timer:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_run_sync, department, question),
                timeout=60,
            )
            answer = str(result.get("output", "")).strip()
            steps = result.get("intermediate_steps") or []
        except asyncio.TimeoutError:
            app_logger.warning(f"SQL agent timed out for {department}: {question[:80]}")
            return _sql_failure(state, timer, "The database query took too long.")
        except Exception as exc:
            app_logger.exception(f"SQL agent failed for {department}")
            return _sql_failure(state, timer, str(exc))

    _audit_generated_sql(state, steps)

    if not answer or answer.lower().startswith("agent stopped"):
        return _sql_failure(
            state, timer, "The database query did not produce a usable result."
        )

    return {
        "answer": answer,
        "citations": [],
        "answer_source": AnswerSource.SQL_AGENT.value,
        "confidence": 0.9,
        "timings": timer.result,
        "trace": ["sql_agent"],
    }


def _sql_failure(state: AgentState, timer: NodeTimer, detail: str) -> dict[str, Any]:
    return {
        "answer": (
            "I could not retrieve that information from the "
            f"{state['department']} document records. Please try rephrasing, or "
            "ask about the content of a specific document instead."
        ),
        "citations": [],
        "answer_source": AnswerSource.REFUSED_NO_EVIDENCE.value,
        "confidence": 0.0,
        "error": detail,
        "timings": timer.result,
        "trace": ["sql_agent:failed"],
    }


def _audit_generated_sql(state: AgentState, steps: list) -> None:
    """Record every SQL statement the model produced.

    Even though the role cannot do damage, an attempt to write DDL is a signal
    worth investigating - it usually means someone is probing the assistant.
    """
    statements: list[str] = []
    for step in steps or []:
        try:
            action = step[0]
            tool_input = getattr(action, "tool_input", None)
            if isinstance(tool_input, dict):
                tool_input = tool_input.get("query") or tool_input.get("__arg1")
            if isinstance(tool_input, str) and "select" in tool_input.lower():
                statements.append(tool_input.strip()[:2000])
        except Exception:
            continue

    if not statements:
        return

    suspicious = [s for s in statements if _FORBIDDEN.search(s)]

    write_audit_event(
        "sql_agent_query",
        username=state.get("username"),
        user_id=state.get("user_id"),
        department=state.get("department"),
        request_id=state.get("request_id"),
        success=not suspicious,
        detail={
            "question": state["question"][:300],
            "statements": statements[:5],
            "forbidden_keywords_detected": bool(suspicious),
        },
    )

    if suspicious:
        app_logger.critical(
            f"SQL agent produced a forbidden statement for "
            f"{state.get('username')} [{state.get('department')}]: "
            f"{suspicious[0][:200]}"
        )
