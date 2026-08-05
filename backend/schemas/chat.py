"""Chat request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.config.config import settings


class ChatRequest(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=settings.MAX_QUESTION_LENGTH,
    )

    # Department to search.
    # Department admins will always use their own department.
    # IT Admin can specify any department.
    department: str | None = None

    #: Omit to start a new conversation.
    conversation_id: UUID | None = None

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Question cannot be empty.")
        return v


class Citation(BaseModel):
    marker: int | None = None
    chunk_id: int
    document_id: int
    document: str
    filename: str
    page: int | None = None
    heading: str | None = None
    section: str | None = None
    snippet: str
    score: float


class Timings(BaseModel):
    """Per-stage latency in milliseconds, surfaced in the UI."""

    gate_ms: float | None = None
    rewrite_ms: float | None = None
    route_ms: float | None = None
    retrieve_ms: float | None = None
    rerank_ms: float | None = None
    generate_ms: float | None = None
    verify_ms: float | None = None
    sql_agent_ms: float | None = None
    total_ms: float | None = None
    #: Time until the first token reached the browser - the number users
    #: actually perceive as "how fast is it".
    first_token_ms: float | None = None


class MessageResponse(BaseModel):
    id: int
    uuid: UUID
    role: str
    content: str
    answer_source: str | None = None
    rewritten_query: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    timings: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    model: str | None = None
    total_latency_ms: float | None = None
    feedback: int | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationSummary(BaseModel):
    id: int
    uuid: UUID
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationDetail(ConversationSummary):
    messages: list[MessageResponse] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    #: 1 = helpful, -1 = not helpful
    rating: int

    @field_validator("rating")
    @classmethod
    def _valid_rating(cls, v: int) -> int:
        if v not in (-1, 1):
            raise ValueError("Rating must be 1 (helpful) or -1 (not helpful).")
        return v


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)


# ---------------------------------------------------------------------------
# Server-sent event payloads
# ---------------------------------------------------------------------------
# The stream emits, in order:
#   start -> stage* -> token* -> citations -> done
# or  start -> stage* -> error

class StreamStart(BaseModel):
    event: str = "start"
    conversation_id: UUID
    message_id: UUID
    request_id: str


class StreamStage(BaseModel):
    """Progress ping so the UI can show what the assistant is doing."""

    event: str = "stage"
    stage: str
    label: str
    elapsed_ms: float


class StreamToken(BaseModel):
    event: str = "token"
    text: str


class StreamCitations(BaseModel):
    event: str = "citations"
    citations: list[Citation]


class StreamDone(BaseModel):
    event: str = "done"
    message_id: UUID
    conversation_id: UUID
    answer_source: str
    confidence: float | None = None
    grounded: bool = True
    rewritten_query: str | None = None
    model: str | None = None
    timings: Timings
    conversation_title: str | None = None


class StreamError(BaseModel):
    event: str = "error"
    message: str
    code: str = "internal_error"
