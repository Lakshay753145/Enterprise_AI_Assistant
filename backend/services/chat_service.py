"""Chat orchestration and server-sent-event streaming.

Drives the LangGraph pipeline and translates its event stream into SSE frames
the browser can render incrementally:

    start -> stage* -> token* -> citations -> done

Only tokens tagged ``final_answer`` are forwarded. The graph runs several other
LLM calls (scope classification, query rewriting, fact-checking) and streaming
those to the user would be noise at best and confusing at worst.

Persistence happens *after* the stream completes, so an aborted or failed
generation never lands in history as if it had succeeded.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.graph import get_compiled_graph
from backend.agents.nodes import FINAL_ANSWER_TAG
from backend.agents.state import initial_state
from backend.config.config import settings
from backend.core.constants import AnswerSource, MessageRole
from backend.core.exceptions import LLMUnavailableError, NotFoundError
from backend.core.logging_config import app_logger, write_audit_event, write_chat_archive
from backend.llm.ollama_client import astructured
from backend.models.chat import Conversation
from backend.models.users import User
from backend.repositories.chat_repository import ChatRepository
from backend.repositories.document_repository import DocumentRepository
from backend.schemas.chat import ChatRequest

#: Node name -> what the UI shows while it runs.
STAGE_LABELS: dict[str, str] = {
    "gate": "Checking scope",
    "rewrite": "Interpreting your question",
    "route": "Choosing a strategy",
    "retrieve": "Searching the knowledge base",
    "rerank": "Ranking the best passages",
    "generate": "Composing the answer",
    "verify": "Verifying against sources",
    "sql_agent": "Querying document records",
    "chitchat": "Composing a reply",
    "refuse_out_of_scope": "Finalising",
    "refuse_unsafe": "Finalising",
    "refuse_no_evidence": "Finalising",
}

_TERMINAL_NODES = {
    "generate",
    "verify",
    "sql_agent",
    "chitchat",
    "refuse_out_of_scope",
    "refuse_unsafe",
    "refuse_no_evidence",
}


class ChatService:
    # ------------------------------------------------------------------
    # Conversation setup
    # ------------------------------------------------------------------
    @staticmethod
    async def resolve_conversation(
        db: AsyncSession, user: User, conversation_id: UUID | None
    ) -> Conversation:
        if conversation_id is not None:
            conversation = await ChatRepository.get_conversation(
                db,
                conversation_id,
                user_id=user.id,
                department=user.department,
            )
            if conversation is None:
                # Deliberately indistinguishable from "belongs to someone else".
                raise NotFoundError("Conversation not found.")
            return conversation

        return await ChatRepository.create_conversation(
            db, user_id=user.id, department=user.department
        )

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------
    @staticmethod
    async def stream(
        db: AsyncSession,
        *,
        user: User,
        request: ChatRequest,
        request_id: str,
        target_department: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield SSE frames as ``{"event": ..., "data": {...}}``."""
        started = time.perf_counter()
        first_token_at: float | None = None

        # -------------------------------------------------
        # Resolve which department should be searched
        # -------------------------------------------------

        if user.department == "IT":
            search_department = target_department or user.department
        else:
            search_department = user.department

        conversation = await ChatService.resolve_conversation(
            db, user, request.conversation_id
        )
        is_new_conversation = conversation.message_count == 0

        history = await ChatRepository.get_history_for_llm(
            db,
            conversation_id=conversation.id,
            user_id=user.id,
            department=user.department,
        )

        user_message = await ChatRepository.add_message(
            db,
            conversation=conversation,
            user_id=user.id,
            department=user.department,
            role=MessageRole.USER.value,
            content=request.question,
        )

        yield {
            "event": "start",
            "data": {
                "conversation_id": str(conversation.uuid),
                "message_id": str(user_message.uuid),
                "request_id": request_id,
            },
        }

        # A department with an empty index would otherwise produce a confusing
        # "not in the knowledge base" for every single question.
        if not await DocumentRepository.has_any_content(db, search_department):
            async for frame in ChatService._empty_knowledge_base(
                db, user, conversation, request, started, request_id, search_department,
            ):
                yield frame
            return

        state = initial_state(
            question=request.question,
            department=search_department,
            role=user.role,
            username=user.username,
            user_id=user.id,
            conversation_id=str(conversation.uuid),
            request_id=request_id,
            session=db,
            history=history,
        )

        graph = get_compiled_graph()
        collected: dict[str, Any] = {}
        answer_parts: list[str] = []
        citations_sent = False

        try:
            async for event in graph.astream_events(state, version="v2"):
                kind = event.get("event")
                name = event.get("name", "")
                tags = event.get("tags") or []

                # --- streamed answer tokens ---------------------------------
                if kind == "on_chat_model_stream" and FINAL_ANSWER_TAG in tags:
                    chunk = event.get("data", {}).get("chunk")
                    text = getattr(chunk, "content", "") if chunk else ""
                    if text:
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                            app_logger.debug(
                                f"First token after "
                                f"{(first_token_at - started) * 1000:.0f} ms"
                            )
                        answer_parts.append(text)
                        yield {"event": "token", "data": {"text": text}}

                # --- node progress -----------------------------------------
                elif kind == "on_chain_start" and name in STAGE_LABELS:
                    yield {
                        "event": "stage",
                        "data": {
                            "stage": name,
                            "label": STAGE_LABELS[name],
                            "elapsed_ms": round(
                                (time.perf_counter() - started) * 1000, 1
                            ),
                        },
                    }

                elif kind == "on_chain_end" and name in STAGE_LABELS:
                    output = event.get("data", {}).get("output")
                    if isinstance(output, dict):
                        collected.update(_merge_state(collected, output))

                    # Send citations the moment the answer node finishes, so
                    # sources appear with the text rather than after it.
                    if (
                        name in _TERMINAL_NODES
                        and not citations_sent
                        and collected.get("citations")
                    ):
                        citations_sent = True
                        yield {
                            "event": "citations",
                            "data": {"citations": collected["citations"]},
                        }

        except LLMUnavailableError as exc:
            app_logger.error(f"LLM unavailable during chat: {exc}")
            yield {
                "event": "error",
                "data": {"message": exc.message, "code": exc.error_code},
            }
            await ChatService._cleanup_failed(db, conversation, is_new_conversation)
            return

        except asyncio.CancelledError:
            # Browser navigated away or aborted the fetch. Nothing to persist.
            app_logger.info(f"Chat stream cancelled by client ({request_id})")
            raise

        except Exception as exc:
            app_logger.exception(f"Chat pipeline failed ({request_id})")
            write_audit_event(
                "chat_pipeline_error",
                username=user.username,
                user_id=user.id,
                department=search_department,
                request_id=request_id,
                success=False,
                detail={"error": str(exc)[:500]},
            )
            yield {
                "event": "error",
                "data": {
                    "message": (
                        "Something went wrong while answering. Please try again."
                    ),
                    "code": "internal_error",
                },
            }
            await ChatService._cleanup_failed(db, conversation, is_new_conversation)
            return

        # ------------------------------------------------------------------
        # Finalise
        # ------------------------------------------------------------------
        answer = collected.get("answer") or "".join(answer_parts)
        if not answer.strip():
            answer = (
                "I was unable to produce an answer for that question. "
                "Please try rephrasing it."
            )
            collected["answer_source"] = AnswerSource.ERROR.value

        # Refusals and SQL results are produced whole rather than streamed, so
        # push the text now - otherwise the user sees stages and then nothing.
        if not answer_parts and answer:
            yield {"event": "token", "data": {"text": answer}}

        citations = collected.get("citations") or []
        if citations and not citations_sent:
            yield {"event": "citations", "data": {"citations": citations}}

        total_ms = round((time.perf_counter() - started) * 1000, 1)
        timings = dict(collected.get("timings") or {})
        timings["total_ms"] = total_ms
        if first_token_at is not None:
            timings["first_token_ms"] = round((first_token_at - started) * 1000, 1)

        answer_source = collected.get("answer_source") or AnswerSource.KNOWLEDGE_BASE.value
        confidence = collected.get("confidence")
        model_used = (
            settings.OLLAMA_MODEL
            if answer_source
            in (AnswerSource.KNOWLEDGE_BASE.value, AnswerSource.SQL_AGENT.value)
            else None
        )

        assistant_message = await ChatRepository.add_message(
            db,
            conversation=conversation,
            user_id=user.id,
            department=user.department,
            role=MessageRole.ASSISTANT.value,
            content=answer,
            answer_source=answer_source,
            rewritten_query=collected.get("search_query"),
            citations=citations,
            timings=timings,
            confidence=confidence,
            model=model_used,
            total_latency_ms=total_ms,
        )

        title = conversation.title
        if is_new_conversation:
            title = await ChatService._title_conversation(
                db, conversation, request.question
            )

        # Permanent on-disk transcript (logs/chat/<Dept>/<user>/<YYYY-MM>.jsonl)
        write_chat_archive(
            username=user.username,
            user_id=user.id,
            department=search_department,
            conversation_id=str(conversation.uuid),
            message_id=str(assistant_message.uuid),
            question=request.question,
            answer=answer,
            answer_source=answer_source,
            citations=citations,
            timings=timings,
            rewritten_query=collected.get("search_query"),
            confidence=confidence,
            model=model_used,
            request_id=request_id,
        )

        app_logger.info(
            f"Answered [{search_department}/{user.username}] in {total_ms:.0f} ms "
            f"source={answer_source} confidence={confidence or 0:.2f} "
            f"trace={'>'.join(collected.get('trace') or [])}"
        )

        yield {
            "event": "done",
            "data": {
                "message_id": str(assistant_message.uuid),
                "conversation_id": str(conversation.uuid),
                "answer_source": answer_source,
                "confidence": confidence,
                "grounded": collected.get("grounded", True),
                "rewritten_query": collected.get("search_query"),
                "model": model_used,
                "timings": timings,
                "conversation_title": title,
            },
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    async def _empty_knowledge_base(
        db: AsyncSession,
        user: User,
        conversation: Conversation,
        request: ChatRequest,
        started: float,
        request_id: str,
        search_department: str,
    ) ->AsyncGenerator[dict[str, Any], None]:
        message = (
            f"The {search_department} knowledge base has not been set up yet - no "
            f"documents have been ingested for your department.\n\nPlease ask "
            f"your department administrator to upload the relevant documentation. "
            f"Until then I have nothing to answer from, and I will not guess."
        )

        yield {"event": "token", "data": {"text": message}}

        total_ms = round((time.perf_counter() - started) * 1000, 1)
        assistant_message = await ChatRepository.add_message(
            db,
            conversation=conversation,
            user_id=user.id,
            department=user.department,
            role=MessageRole.ASSISTANT.value,
            content=message,
            answer_source=AnswerSource.REFUSED_NO_EVIDENCE.value,
            timings={"total_ms": total_ms},
            confidence=0.0,
            total_latency_ms=total_ms,
        )

        app_logger.warning(
            f"{search_department} has no ingested documents; refused question from "
            f"{user.username}"
        )

        yield {
            "event": "done",
            "data": {
                "message_id": str(assistant_message.uuid),
                "conversation_id": str(conversation.uuid),
                "answer_source": AnswerSource.REFUSED_NO_EVIDENCE.value,
                "confidence": 0.0,
                "grounded": True,
                "rewritten_query": None,
                "model": None,
                "timings": {"total_ms": total_ms},
                "conversation_title": conversation.title,
            },
        }

    @staticmethod
    async def _title_conversation(
        db: AsyncSession, conversation: Conversation, question: str
    ) -> str:
        """Name a new conversation from its opening question."""
        from langchain_core.messages import HumanMessage, SystemMessage
        from pydantic import BaseModel

        from backend.prompts.templates import TITLE_SYSTEM

        class TitleOutput(BaseModel):
            title: str = ""

        fallback = " ".join(question.split())[:60]

        try:
            result = await astructured(
                [
                    SystemMessage(content=TITLE_SYSTEM),
                    HumanMessage(content=question[:500]),
                ],
                TitleOutput,
                fast=True,
                default=TitleOutput(title=fallback),
            )
            title = (result.title or fallback).strip().strip('"').strip()
        except Exception:
            title = fallback

        title = title[:300] or fallback
        await ChatRepository.rename_conversation(db, conversation, title)
        return title

    @staticmethod
    async def _cleanup_failed(
        db: AsyncSession, conversation: Conversation, was_new: bool
    ) -> None:
        """Do not leave an empty 'New chat' in the sidebar after a failure."""
        if not was_new:
            return
        try:
            await ChatRepository.prune_empty_conversation(db, conversation)
        except Exception as exc:  # pragma: no cover
            app_logger.warning(f"Could not prune failed conversation: {exc}")


def _merge_state(existing: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Merge a node's output into the accumulated state.

    `timings` and `trace` accumulate; everything else is last-write-wins,
    matching the reducers declared on AgentState.
    """
    merged = dict(update)

    if "timings" in update:
        merged["timings"] = {**(existing.get("timings") or {}), **update["timings"]}
    if "trace" in update:
        merged["trace"] = list(existing.get("trace") or []) + list(update["trace"])

    return merged
