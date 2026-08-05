"""Administration routes.

Two tiers:

* **Department admin** - manages users, documents and analytics for their own
  department only. Every query here is filtered by `actor.department`.
* **Super admin** - cross-department views and the diagnostics page.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status

from backend.core.constants import API_PREFIX, Department, Role
from backend.core.exceptions import NotFoundError, PermissionDeniedError
from backend.core.logging_config import read_chat_archive, write_audit_event
from backend.repositories.chat_repository import ChatRepository
from backend.repositories.document_repository import DocumentRepository
from backend.repositories.user_repository import UserRepository
from backend.schemas.auth import UserCreate, UserResponse
from backend.security.dependencies import (
    AdminUser,
    DbSession,
    SuperAdminUser,
    audit_context,
)
from backend.security.isolation import assert_department_match
from backend.services.auth_services import AuthService

router = APIRouter(prefix=f"{API_PREFIX}/admin", tags=["Administration"])


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@router.get(
    "/users",
    response_model=list[UserResponse],
    summary="List users in your department",
)
async def list_users(
    actor: AdminUser, db: DbSession, include_inactive: bool = False
):
    if actor.is_super_admin:
        users = await UserRepository.list_all(db)
    else:
        users = await UserRepository.list_by_department(
            db, actor.department, include_inactive=include_inactive
        )
    return [UserResponse.model_validate(u) for u in users]


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user in your department",
)
async def create_user(
    payload: UserCreate, request: Request, actor: AdminUser, db: DbSession
):
    """A department admin may create standard users in their own department.

    Granting an administrative role, or creating a user elsewhere, requires a
    super admin - enforced inside AuthService.
    """
    user = await AuthService.register(
        db, payload, created_by=actor, audit=audit_context(request, actor)
    )
    return UserResponse.model_validate(user)


@router.post(
    "/users/{user_id}/deactivate",
    response_model=UserResponse,
    summary="Deactivate a user",
)
async def deactivate_user(
    user_id: int, request: Request, actor: AdminUser, db: DbSession
):
    target = await _resolve_target_user(db, user_id, actor)

    if target.id == actor.id:
        raise PermissionDeniedError("You cannot deactivate your own account.")

    target = await UserRepository.set_active(db, target, False)
    write_audit_event(
        "user_deactivated",
        detail={"target_username": target.username, "target_id": target.id},
        **audit_context(request, actor),
    )
    return UserResponse.model_validate(target)


@router.post(
    "/users/{user_id}/activate",
    response_model=UserResponse,
    summary="Reactivate a user",
)
async def activate_user(
    user_id: int, request: Request, actor: AdminUser, db: DbSession
):
    target = await _resolve_target_user(db, user_id, actor)
    target = await UserRepository.set_active(db, target, True)
    write_audit_event(
        "user_activated",
        detail={"target_username": target.username, "target_id": target.id},
        **audit_context(request, actor),
    )
    return UserResponse.model_validate(target)


@router.post(
    "/users/{user_id}/unlock",
    response_model=UserResponse,
    summary="Clear a login lockout",
)
async def unlock_user(
    user_id: int, request: Request, actor: AdminUser, db: DbSession
):
    target = await _resolve_target_user(db, user_id, actor)
    target.failed_login_attempts = 0
    target.locked_until = None
    await db.commit()
    await db.refresh(target)

    write_audit_event(
        "user_unlocked",
        detail={"target_username": target.username},
        **audit_context(request, actor),
    )
    return UserResponse.model_validate(target)


async def _resolve_target_user(db: DbSession, user_id: int, actor):
    target = await UserRepository.get_by_id(db, user_id)
    if target is None:
        raise NotFoundError("User not found.")

    # A department admin may only act on their own department's users.
    assert_department_match(
        target.department,
        department=actor.department,
        role=actor.role,
        username=actor.username,
    )

    if target.role == Role.SUPER_ADMIN.value and not actor.is_super_admin:
        raise PermissionDeniedError("You cannot modify a super administrator.")

    return target


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@router.get("/analytics", summary="Usage analytics for your department")
async def analytics(actor: AdminUser, db: DbSession) -> dict[str, Any]:
    chat_stats = await ChatRepository.department_stats(db, actor.department)
    doc_stats = await DocumentRepository.summary(db, actor.department)
    users = await UserRepository.list_by_department(db, actor.department)

    return {
        "department": actor.department,
        "users": {
            "total": len(users),
            "admins": sum(1 for u in users if u.is_admin),
        },
        "documents": doc_stats,
        "chat": chat_stats,
    }


@router.get(
    "/users/{username}/transcript",
    summary="Read a user's archived conversation transcript",
)
async def user_transcript(
    username: str,
    request: Request,
    actor: AdminUser,
    db: DbSession,
    limit: int = 200,
) -> dict[str, Any]:
    """Read the on-disk JSONL archive for one user.

    Reading someone's conversations is itself a sensitive act, so it is
    audited like any other privileged access.
    """
    target = await UserRepository.get_by_username(db, username)
    if target is None:
        raise NotFoundError("User not found.")

    assert_department_match(
        target.department,
        department=actor.department,
        role=actor.role,
        username=actor.username,
    )

    write_audit_event(
        "transcript_accessed",
        detail={"target_username": target.username},
        **audit_context(request, actor),
    )

    records = read_chat_archive(
        target.department, target.username, limit=min(limit, 1000)
    )
    return {
        "username": target.username,
        "department": target.department,
        "count": len(records),
        "records": records,
    }


# ---------------------------------------------------------------------------
# Super-admin only
# ---------------------------------------------------------------------------

@router.get(
    "/overview",
    summary="Cross-department overview (super administrators only)",
)
async def overview(actor: SuperAdminUser, db: DbSession) -> dict[str, Any]:
    departments = []
    for name in Department.values():
        departments.append(
            {
                "department": name,
                "documents": await DocumentRepository.summary(db, name),
                "chat": await ChatRepository.department_stats(db, name),
            }
        )

    return {
        "users_by_department": await UserRepository.count_by_department(db),
        "departments": departments,
    }


@router.get(
    "/pipeline",
    summary="Retrieval pipeline diagram and configuration (super administrators only)",
)
async def pipeline(actor: SuperAdminUser) -> dict[str, Any]:
    from backend.agents.graph import render_mermaid
    from backend.config.config import settings

    return {
        "mermaid": render_mermaid(),
        "configuration": {
            "answer_model": settings.OLLAMA_MODEL,
            "fast_model": settings.OLLAMA_FAST_MODEL,
            "embedding_model": settings.EMBEDDING_MODEL,
            "embedding_dimension": settings.EMBEDDING_DIMENSION,
            "reranker_model": settings.RERANKER_MODEL if settings.RERANKER_ENABLED
            else None,
            "chunk_max_tokens": settings.CHUNK_MAX_TOKENS,
            "vector_top_k": settings.VECTOR_TOP_K,
            "keyword_top_k": settings.KEYWORD_TOP_K,
            "rrf_k": settings.RRF_K,
            "rerank_candidates": settings.RERANK_CANDIDATES,
            "final_top_k": settings.FINAL_TOP_K,
            "rerank_score_threshold": settings.RERANK_SCORE_THRESHOLD,
            "min_confidence_threshold": settings.MIN_CONFIDENCE_THRESHOLD,
            "relevance_gate": settings.RELEVANCE_GATE_ENABLED,
            "grounding_check": settings.GROUNDING_CHECK_ENABLED,
            "sql_agent": settings.SQL_AGENT_ENABLED,
        },
    }
