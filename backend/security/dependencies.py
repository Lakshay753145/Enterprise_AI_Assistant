"""FastAPI authentication and authorisation dependencies.

`get_current_user` is the single door into every protected route. It does four
things, in order, and all four matter:

  1. decode + validate the bearer token
  2. load the user by primary key and confirm they are still active
  3. confirm the token's department still matches the database - so moving
     someone between departments invalidates their existing tokens instead of
     leaving them with lingering access to their old department
  4. bind the request's transaction to that department via Postgres RLS
"""

from __future__ import annotations

from typing import Annotated, Iterable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.constants import DOCUMENT_MANAGER_ROLES, Role
from backend.core.exceptions import (
    AccountInactiveError,
    PermissionDeniedError,
    TokenError,
)
from backend.core.logging_config import app_logger, write_audit_event
from backend.database.database import apply_rls_context, get_db
from backend.models.users import User
from backend.security.jwt_handler import TokenPayload, decode_token

# auto_error=False so a missing header raises our typed TokenError (and the
# JSON error shape the frontend expects) instead of FastAPI's default 403.
bearer_scheme = HTTPBearer(auto_error=False)


async def get_token_payload(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
) -> TokenPayload:
    if credentials is None or not credentials.credentials:
        raise TokenError("Authentication required.")
    return decode_token(credentials.credentials, expected_type="access")


async def get_current_user(
    request: Request,
    payload: Annotated[TokenPayload, Depends(get_token_payload)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    result = await db.execute(select(User).where(User.id == payload.uid))
    user = result.scalar_one_or_none()

    if user is None:
        write_audit_event(
            "auth_token_unknown_user",
            username=payload.sub,
            user_id=payload.uid,
            department=payload.dept,
            ip_address=_client_ip(request),
            request_id=_request_id(request),
            success=False,
        )
        raise TokenError("Account no longer exists.")

    if not user.is_active:
        raise AccountInactiveError()

    # A token minted before a department transfer must not keep working.
    if user.department != payload.dept or user.role != payload.role:
        app_logger.warning(
            f"Token claims stale for {user.username}: "
            f"token dept={payload.dept}/role={payload.role}, "
            f"db dept={user.department}/role={user.role}"
        )
        write_audit_event(
            "auth_stale_token_claims",
            username=user.username,
            user_id=user.id,
            department=user.department,
            role=user.role,
            ip_address=_client_ip(request),
            request_id=_request_id(request),
            success=False,
            detail={"token_department": payload.dept, "token_role": payload.role},
        )
        raise TokenError(
            "Your access profile has changed. Please sign in again."
        )

    # Layer 3 of isolation: tell Postgres who this transaction belongs to.
    await apply_rls_context(db, department=user.department, role=user.role)

    # Stash on request.state so middleware and loggers can annotate without
    # re-resolving the dependency.
    request.state.user_id = user.id
    request.state.username = user.username
    request.state.department = user.department
    request.state.role = user.role

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


# ---------------------------------------------------------------------------
# Role gates
# ---------------------------------------------------------------------------

def require_roles(*allowed: str | Role):
    """Dependency factory restricting a route to the given roles."""
    allowed_values = {r.value if isinstance(r, Role) else r for r in allowed}

    async def _guard(request: Request, user: CurrentUser) -> User:
        if user.role not in allowed_values:
            write_audit_event(
                "authorization_denied",
                username=user.username,
                user_id=user.id,
                department=user.department,
                role=user.role,
                ip_address=_client_ip(request),
                request_id=_request_id(request),
                success=False,
                detail={
                    "path": str(request.url.path),
                    "required_roles": sorted(allowed_values),
                },
            )
            raise PermissionDeniedError(
                "This action requires "
                + " or ".join(sorted(allowed_values))
                + " privileges."
            )
        return user

    return _guard


require_admin = require_roles(Role.ADMIN, Role.SUPER_ADMIN)
require_super_admin = require_roles(Role.SUPER_ADMIN)

AdminUser = Annotated[User, Depends(require_admin)]
SuperAdminUser = Annotated[User, Depends(require_super_admin)]


def can_manage_documents(
    user: User,
    target_department: str | None = None,
) -> bool:
    """
    Department admins:
        Can manage only their own department.

    IT admins:
        Can manage every department.
    """

    if user.role not in {r.value for r in DOCUMENT_MANAGER_ROLES}:
        return False

    # No department specified → just checking upload permission.
    if target_department is None:
        return True

    # IT can upload anywhere.
    if user.department.upper() == "IT":
        return True

    # Other admins only to their own department.
    return user.department == target_department

def can_upload_to_department(user: User, target_department: str) -> bool:
    """
    Permission matrix

    SUPER_ADMIN
        -> any department

    IT ADMIN
        -> any department

    Other ADMIN
        -> only own department
    """

    if user.role == Role.SUPER_ADMIN.value:
        return True

    if (
        user.role == Role.ADMIN.value
        and user.department == "IT"
    ):
        return True

    if (
        user.role == Role.ADMIN.value
        and user.department == target_department
    ):
        return True

    return False


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------

def _client_ip(request: Request) -> str | None:
    """Best-effort client IP, honouring one layer of reverse proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def audit_context(request: Request, user: User | None = None) -> dict:
    """Common audit fields for a request, ready to splat into write_audit_event."""
    ctx = {
        "ip_address": _client_ip(request),
        "request_id": _request_id(request),
    }
    if user is not None:
        ctx.update(
            {
                "username": user.username,
                "user_id": user.id,
                "department": user.department,
                "role": user.role,
            }
        )
    return ctx


__all__: Iterable[str] = [
    "AdminUser",
    "CurrentUser",
    "DbSession",
    "SuperAdminUser",
    "audit_context",
    "can_manage_documents",
    "get_current_user",
    "get_token_payload",
    "require_admin",
    "require_roles",
    "require_super_admin",
    "can_upload_to_department",
]
