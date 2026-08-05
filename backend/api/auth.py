"""Authentication routes."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from backend.core.constants import API_PREFIX
from backend.schemas.auth import (
    DepartmentInfo,
    PasswordChange,
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from backend.security.dependencies import (
    CurrentUser,
    DbSession,
    SuperAdminUser,
    audit_context,
)
from backend.services.auth_services import AuthService

router = APIRouter(prefix=f"{API_PREFIX}/auth", tags=["Authentication"])


@router.get(
    "/departments",
    response_model=list[DepartmentInfo],
    summary="List departments for the sign-in form",
)
async def list_departments() -> list[dict[str, str]]:
    """Public: the login screen needs this before a token exists."""
    return AuthService.list_departments()


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Sign in with username, password and department",
)
async def login(payload: UserLogin, request: Request, db: DbSession):
    return await AuthService.login(db, payload, audit=audit_context(request))


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new access token",
)
async def refresh(payload: RefreshRequest, request: Request, db: DbSession):
    return await AuthService.refresh(
        db, payload.refresh_token, audit=audit_context(request)
    )


@router.get("/me", response_model=UserResponse, summary="Current signed-in user")
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change your own password",
)
async def change_password(
    payload: PasswordChange, request: Request, user: CurrentUser, db: DbSession
) -> None:
    await AuthService.change_password(
        db,
        user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        audit=audit_context(request, user),
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a new account (super administrators only)",
)
async def register(
    payload: UserCreate,
    request: Request,
    actor: SuperAdminUser,
    db: DbSession,
) -> UserResponse:
    """Account creation is deliberately closed.

    There is no self-service signup: an account grants access to a department's
    confidential documentation, so it is provisioned by IT, not requested by
    the person who wants it. Department admins provision within their own
    department via the /admin routes.
    """
    user = await AuthService.register(
        db, payload, created_by=actor, audit=audit_context(request, actor)
    )
    return UserResponse.model_validate(user)
