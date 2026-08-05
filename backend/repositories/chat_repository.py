"""Conversation and message data access.

Every method takes a `user_id` **and** a `department` and filters on both. The
user_id alone would be sufficient in a correct system; the department filter is
there so that a bug in user resolution still cannot surface another
department's conversations.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.config import settings
from backend.core.constants import MessageRole
from backend.models.chat import Conversation, Message


class ChatRepository:
    # -- conversations -----------------------------------------------------
    @staticmethod
    async def create_conversation(
        db: AsyncSession, *, user_id: int, department: str, title: str = "New chat"
    ) -> Conversation:
        conversation = Conversation(
            user_id=user_id, department=department, title=title[:300]
        )
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)
        return conversation

    @staticmethod
    async def get_conversation(
        db: AsyncSession,
        conversation_uuid: UUID,
        *,
        user_id: int,
        department: str,
    ) -> Conversation | None:
        return (
            await db.execute(
                select(Conversation).where(
                    Conversation.uuid == conversation_uuid,
                    Conversation.user_id == user_id,
                    Conversation.department == department,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_conversations(
        db: AsyncSession,
        *,
        user_id: int,
        department: str,
        limit: int = 50,
        include_archived: bool = False,
    ) -> list[Conversation]:
        stmt = select(Conversation).where(
            Conversation.user_id == user_id,
            Conversation.department == department,
        )
        if not include_archived:
            stmt = stmt.where(Conversation.is_archived.is_(False))

        result = await db.execute(
            stmt.order_by(desc(Conversation.updated_at)).limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def rename_conversation(
        db: AsyncSession, conversation: Conversation, title: str
    ) -> Conversation:
        conversation.title = title[:300]
        await db.commit()
        await db.refresh(conversation)
        return conversation

    @staticmethod
    async def archive_conversation(
        db: AsyncSession, conversation: Conversation
    ) -> None:
        conversation.is_archived = True
        await db.commit()

    @staticmethod
    async def delete_conversation(
        db: AsyncSession, conversation: Conversation
    ) -> None:
        await db.delete(conversation)
        await db.commit()

    # -- messages ----------------------------------------------------------
    @staticmethod
    async def add_message(
        db: AsyncSession,
        *,
        conversation: Conversation,
        user_id: int,
        department: str,
        role: str,
        content: str,
        answer_source: str | None = None,
        rewritten_query: str | None = None,
        citations: list[dict[str, Any]] | None = None,
        timings: dict[str, Any] | None = None,
        confidence: float | None = None,
        model: str | None = None,
        total_latency_ms: float | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation.id,
            user_id=user_id,
            department=department,
            role=role,
            content=content,
            answer_source=answer_source,
            rewritten_query=rewritten_query,
            citations=citations or [],
            timings=timings or {},
            confidence=confidence,
            model=model,
            total_latency_ms=total_latency_ms,
        )
        db.add(message)

        conversation.message_count = (conversation.message_count or 0) + 1
        await db.commit()
        await db.refresh(message)
        return message

    @staticmethod
    async def get_messages(
        db: AsyncSession,
        *,
        conversation_id: int,
        user_id: int,
        department: str,
        limit: int | None = None,
    ) -> list[Message]:
        """Return the most recent `limit` messages, in chronological order.

        Defaults to CHAT_HISTORY_LIMIT (10). The ORDER BY DESC + LIMIT then
        reverse pattern is deliberate: it keeps the *newest* N, whereas an
        ascending LIMIT would return the oldest N and lose the live context.
        """
        limit = limit or settings.CHAT_HISTORY_LIMIT

        result = await db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.user_id == user_id,
                Message.department == department,
            )
            .order_by(desc(Message.created_at), desc(Message.id))
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    @staticmethod
    async def get_history_for_llm(
        db: AsyncSession,
        *,
        conversation_id: int,
        user_id: int,
        department: str,
        limit: int | None = None,
    ) -> list[dict[str, str]]:
        """Prior turns in the shape the agent graph expects."""
        messages = await ChatRepository.get_messages(
            db,
            conversation_id=conversation_id,
            user_id=user_id,
            department=department,
            limit=limit or settings.CHAT_HISTORY_WINDOW,
        )
        return [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in (MessageRole.USER.value, MessageRole.ASSISTANT.value)
        ]

    @staticmethod
    async def get_message_by_uuid(
        db: AsyncSession, message_uuid: UUID, *, user_id: int, department: str
    ) -> Message | None:
        return (
            await db.execute(
                select(Message).where(
                    Message.uuid == message_uuid,
                    Message.user_id == user_id,
                    Message.department == department,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def set_feedback(
        db: AsyncSession, message: Message, rating: int
    ) -> Message:
        message.feedback = rating
        await db.commit()
        await db.refresh(message)
        return message

    # -- analytics ---------------------------------------------------------
    @staticmethod
    async def department_stats(
        db: AsyncSession, department: str
    ) -> dict[str, Any]:
        row = (
            await db.execute(
                select(
                    func.count(Message.id),
                    func.avg(Message.total_latency_ms),
                    func.avg(Message.confidence),
                ).where(
                    Message.department == department,
                    Message.role == MessageRole.ASSISTANT.value,
                )
            )
        ).one()

        refusals = (
            await db.execute(
                select(func.count(Message.id)).where(
                    Message.department == department,
                    Message.answer_source.in_(
                        ["refused_no_evidence", "refused_out_of_scope"]
                    ),
                )
            )
        ).scalar_one()

        helpful = (
            await db.execute(
                select(func.count(Message.id)).where(
                    Message.department == department, Message.feedback == 1
                )
            )
        ).scalar_one()

        unhelpful = (
            await db.execute(
                select(func.count(Message.id)).where(
                    Message.department == department, Message.feedback == -1
                )
            )
        ).scalar_one()

        total = row[0] or 0
        return {
            "total_answers": total,
            "avg_latency_ms": round(float(row[1]), 1) if row[1] else None,
            "avg_confidence": round(float(row[2]), 3) if row[2] else None,
            "refusals": refusals,
            "refusal_rate": round(refusals / total, 3) if total else 0.0,
            "feedback_helpful": helpful,
            "feedback_unhelpful": unhelpful,
        }

    @staticmethod
    async def prune_empty_conversation(
        db: AsyncSession, conversation: Conversation
    ) -> None:
        """Delete a conversation that never received a message.

        Happens when a request fails before the first exchange is stored; the
        sidebar should not fill with empty "New chat" entries.
        """
        count = (
            await db.execute(
                select(func.count(Message.id)).where(
                    Message.conversation_id == conversation.id
                )
            )
        ).scalar_one()
        if count == 0:
            await db.delete(conversation)
            await db.commit()

    @staticmethod
    async def touch(db: AsyncSession, conversation_id: int) -> None:
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=func.now())
        )
        await db.commit()
