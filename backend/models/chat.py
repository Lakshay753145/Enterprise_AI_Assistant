"""Conversations and messages.

Both tables carry `department` so RLS can enforce isolation on chat data too -
a user's questions and the excerpts quoted back to them are themselves
department-confidential.
"""

from __future__ import annotations

import uuid as uuid_pkg
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.constants import Department, MessageRole
from backend.database.database import Base

if TYPE_CHECKING:
    from backend.models.users import User


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False, default=uuid_pkg.uuid4
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    #: Auto-derived from the first question; the user can rename it.
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="New chat")

    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.created_at",
    )

    __table_args__ = (
        CheckConstraint(
            "department IN ('" + "','".join(Department.values()) + "')",
            name="ck_conversations_department_valid",
        ),
        # The sidebar query is: my conversations, newest first.
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
        Index("ix_conversations_dept_user", "department", "user_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Conversation id={self.id} user={self.user_id} {self.title!r}>"


class Message(Base):
    """A single turn.

    Assistant rows carry the full provenance of the answer: which chunks were
    cited, how confident the reranker was, how long each pipeline stage took,
    and which model produced it. That is what makes an answer auditable months
    later when someone asks "why did it say that?".
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False, default=uuid_pkg.uuid4
    )

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # --- Assistant-only provenance ------------------------------------------
    answer_source: Mapped[str | None] = mapped_column(String(40))
    #: The technical reformulation the retriever actually searched with.
    rewritten_query: Mapped[str | None] = mapped_column(Text)
    #: [{document, page, heading, snippet, score}, ...]
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: {"rewrite": 120.4, "retrieval": 88.1, "rerank": 210.0, "generation": 3400.2,
    #:  "total": 3818.7} - milliseconds.
    timings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    model: Mapped[str | None] = mapped_column(String(120))
    total_latency_ms: Mapped[float | None] = mapped_column(Float)
    token_count: Mapped[int | None] = mapped_column(Integer)

    #: Thumbs up / down from the UI. NULL = no feedback given.
    feedback: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    user: Mapped["User"] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint(
            "role IN ('" + "','".join(r.value for r in MessageRole) + "')",
            name="ck_messages_role_valid",
        ),
        CheckConstraint(
            "department IN ('" + "','".join(Department.values()) + "')",
            name="ck_messages_department_valid",
        ),
        CheckConstraint(
            "feedback IS NULL OR feedback IN (-1, 1)",
            name="ck_messages_feedback_valid",
        ),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_messages_user_created", "user_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        preview = self.content[:50].replace("\n", " ")
        return f"<Message id={self.id} role={self.role!r} {preview!r}...>"
