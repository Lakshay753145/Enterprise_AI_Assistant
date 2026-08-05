"""Chat routes: streaming answers and conversation history."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Request, status
from sse_starlette.sse import EventSourceResponse

from backend.config.config import settings
from backend.core.constants import API_PREFIX
from backend.core.exceptions import AppError, NotFoundError
from backend.core.logging_config import app_logger
from backend.repositories.chat_repository import ChatRepository
from backend.schemas.chat import (
    ChatRequest,
    ConversationDetail,
    ConversationSummary,
    FeedbackRequest,
    MessageResponse,
    RenameConversationRequest,
)
from backend.security.dependencies import CurrentUser, DbSession
from backend.services.chat_service import ChatService

router = APIRouter(prefix=f"{API_PREFIX}/chat", tags=["Chat"])


@router.post(
    "/stream",
    summary="Ask a question and stream the answer",
    response_class=EventSourceResponse,
)
async def stream_chat(
    payload: ChatRequest, request: Request, user: CurrentUser, db: DbSession
):
    """Server-sent events: `start`, `stage`, `token`, `citations`, `done`.

    On error the stream emits a single `error` frame and closes; the HTTP
    status stays 200 because the response body has already begun.
    """
    request_id = getattr(request.state, "request_id", "-")

    async def generate() -> AsyncGenerator[dict[str, str], None]:
        try:
            async for frame in ChatService.stream(
                db, user=user, request=payload, request_id=request_id,
                  target_department=(
        payload.department
        if hasattr(payload, "department")
        else None
    ),
            ):
                yield {
                    "event": frame["event"],
                    "data": json.dumps(frame["data"], default=str),
                }
        except AppError as exc:
            app_logger.warning(f"Chat stream error ({request_id}): {exc.message}")
            yield {
                "event": "error",
                "data": json.dumps(
                    {"message": exc.message, "code": exc.error_code}
                ),
            }
        except Exception:
            app_logger.exception(f"Unhandled chat stream error ({request_id})")
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "message": "An unexpected error occurred.",
                        "code": "internal_error",
                    }
                ),
            }

    return EventSourceResponse(
        generate(),
        headers={
            # nginx buffers proxied responses by default, which would hold the
            # whole answer back and destroy the streaming effect.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-transform",
        },
        ping=15,
    )


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

@router.get(
    "/conversations",
    response_model=list[ConversationSummary],
    summary="List your conversations",
)
async def list_conversations(user: CurrentUser, db: DbSession, limit: int = 50):
    conversations = await ChatRepository.list_conversations(
        db,
        user_id=user.id,
        department=user.department,
        limit=min(limit, 200),
    )
    return [ConversationSummary.model_validate(c) for c in conversations]


@router.get(
    "/conversations/{conversation_uuid}",
    response_model=ConversationDetail,
    summary="Get one conversation with its recent messages",
)
async def get_conversation(
    conversation_uuid: UUID,
    user: CurrentUser,
    db: DbSession,
    limit: int | None = None,
):
    """Returns the most recent `CHAT_HISTORY_LIMIT` messages by default."""
    conversation = await ChatRepository.get_conversation(
        db, conversation_uuid, user_id=user.id, department=user.department
    )
    if conversation is None:
        raise NotFoundError("Conversation not found.")

    messages = await ChatRepository.get_messages(
        db,
        conversation_id=conversation.id,
        user_id=user.id,
        department=user.department,
        limit=min(limit or settings.CHAT_HISTORY_LIMIT, 200),
    )

    # NOTE: We build the ConversationDetail fields explicitly instead of
    # calling ConversationDetail.model_validate(conversation) directly.
    # That single call used to crash with a MissingGreenlet error because
    # Pydantic would try to read conversation.messages (a lazy-loaded
    # SQLAlchemy relationship) outside of an active async session context.
    # By excluding "messages" here and setting it separately from the
    # already-fetched `messages` list below, we avoid touching that
    # unloaded relationship entirely.
    conversation_data = {
        field: getattr(conversation, field)
        for field in ConversationDetail.model_fields
        if field != "messages"
    }
    detail = ConversationDetail(**conversation_data, messages=[])
    detail.messages = [MessageResponse.model_validate(m) for m in messages]
    return detail


@router.patch(
    "/conversations/{conversation_uuid}",
    response_model=ConversationSummary,
    summary="Rename a conversation",
)
async def rename_conversation(
    conversation_uuid: UUID,
    payload: RenameConversationRequest,
    user: CurrentUser,
    db: DbSession,
):
    conversation = await ChatRepository.get_conversation(
        db, conversation_uuid, user_id=user.id, department=user.department
    )
    if conversation is None:
        raise NotFoundError("Conversation not found.")

    conversation = await ChatRepository.rename_conversation(
        db, conversation, payload.title
    )
    return ConversationSummary.model_validate(conversation)


@router.delete(
    "/conversations/{conversation_uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation",
)
async def delete_conversation(
    conversation_uuid: UUID, user: CurrentUser, db: DbSession
) -> None:
    conversation = await ChatRepository.get_conversation(
        db, conversation_uuid, user_id=user.id, department=user.department
    )
    if conversation is None:
        raise NotFoundError("Conversation not found.")

    # The database row goes; the on-disk transcript in logs/chat/ is retained
    # as the permanent record.
    await ChatRepository.delete_conversation(db, conversation)


@router.post(
    "/messages/{message_uuid}/feedback",
    response_model=MessageResponse,
    summary="Rate an answer",
)
async def submit_feedback(
    message_uuid: UUID,
    payload: FeedbackRequest,
    user: CurrentUser,
    db: DbSession,
):
    message = await ChatRepository.get_message_by_uuid(
        db, message_uuid, user_id=user.id, department=user.department
    )
    if message is None:
        raise NotFoundError("Message not found.")

    message = await ChatRepository.set_feedback(db, message, payload.rating)
    return MessageResponse.model_validate(message)