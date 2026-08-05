"""Authentication service.

Login takes username + password + **department**, and all three must match.
The department is not a hint about which account to look up - the account is
found by username. It is a third assertion the caller has to get right, and a
mismatch is treated exactly like a wrong password: same generic error, same
failed-attempt counter, no disclosure of which field was wrong.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.config import settings
from backend.core.constants import Department, Role
from backend.core.exceptions import (
    AccountInactiveError,
    AccountLockedError,
    AuthenticationError,
    ConflictError,
    PermissionDeniedError,
    TokenError,
    ValidationError,
)
from backend.core.logging_config import app_logger, write_audit_event
from backend.models.users import User
from backend.repositories.user_repository import UserRepository
from backend.schemas.auth import UserCreate, UserLogin
from backend.security.hash import (
    dummy_verify,
    hash_password,
    needs_rehash,
    verify_password,
)
from backend.security.jwt_handler import (
    create_access_token,
    create_refresh_token,
    decode_token,
)


class AuthService:
    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    @staticmethod
    async def register(
        db: AsyncSession,
        data: UserCreate,
        *,
        created_by: User | None = None,
        audit: dict[str, Any] | None = None,
    ) -> User:
        """Create an account.

        Provisioning is an administrative act. A department admin may only
        create accounts inside their own department, and may not mint another
        admin - only a super admin can do either.
        """
        if created_by is not None:
            AuthService._authorise_provisioning(created_by, data)

        if await UserRepository.get_by_username(db, data.username):
            raise ConflictError(f"Username '{data.username}' is already taken.")
        if await UserRepository.get_by_email(db, data.email):
            raise ConflictError(f"Email '{data.email}' is already registered.")

        user = User(
            username=data.username,
            email=data.email.lower(),
            full_name=data.full_name,
            password_hash=hash_password(data.password),
            department=data.department,
            role=data.role,
            is_active=True,
        )
        user = await UserRepository.create(db, user)

        write_audit_event(
            "user_registered",
            username=user.username,
            user_id=user.id,
            department=user.department,
            role=user.role,
            detail={
                "email": user.email,
                "created_by": created_by.username if created_by else "self",
            },
            **(audit or {}),
        )
        app_logger.info(
            f"Registered {user.username} in {user.department} as {user.role}"
        )
        return user

    @staticmethod
    def _authorise_provisioning(actor: User, data: UserCreate) -> None:
        if actor.is_super_admin:
            return
        if not actor.is_admin:
            raise PermissionDeniedError("Only administrators can create accounts.")
        if data.department != actor.department:
            raise PermissionDeniedError(
                "You can only create accounts within your own department."
            )
        if data.role != Role.USER.value:
            raise PermissionDeniedError(
                "Only a super administrator can grant administrative roles."
            )

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    @staticmethod
    async def login(
        db: AsyncSession, data: UserLogin, *, audit: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        audit = audit or {}
        user = await UserRepository.get_by_username(db, data.username)

        if user is None:
            # Spend the same time as a real bcrypt verification so response
            # timing does not reveal whether the username exists.
            dummy_verify()
            write_audit_event(
                "login_failed",
                username=data.username,
                department=data.department,
                success=False,
                detail={"reason": "unknown_username"},
                **audit,
            )
            raise AuthenticationError()

        if user.is_locked:
            write_audit_event(
                "login_blocked_locked",
                username=user.username,
                user_id=user.id,
                department=user.department,
                success=False,
                **audit,
            )
            raise AccountLockedError(
                f"Account locked after repeated failed sign-in attempts. "
                f"Try again in {settings.LOGIN_LOCKOUT_MINUTES} minutes."
            )

        if not user.is_active:
            write_audit_event(
                "login_blocked_inactive",
                username=user.username,
                user_id=user.id,
                department=user.department,
                success=False,
                **audit,
            )
            raise AccountInactiveError()

        password_ok = verify_password(data.password, user.password_hash)
        department_ok = user.department == data.department

        if not (password_ok and department_ok):
            locked = await UserRepository.record_failed_login(db, user)
            write_audit_event(
                "login_failed",
                username=user.username,
                user_id=user.id,
                department=user.department,
                success=False,
                detail={
                    "reason": "bad_password" if not password_ok else "wrong_department",
                    "attempted_department": data.department,
                    "locked": locked,
                },
                **audit,
            )
            if locked:
                raise AccountLockedError(
                    f"Too many failed attempts. Account locked for "
                    f"{settings.LOGIN_LOCKOUT_MINUTES} minutes."
                )
            # One generic message: never confirm which field was wrong.
            raise AuthenticationError()

        # Opportunistically upgrade the hash if bcrypt parameters have changed.
        if needs_rehash(user.password_hash):
            await UserRepository.set_password_hash(
                db, user, hash_password(data.password)
            )

        await UserRepository.record_successful_login(db, user)

        write_audit_event(
            "login_success",
            username=user.username,
            user_id=user.id,
            department=user.department,
            role=user.role,
            **audit,
        )
        app_logger.info(f"{user.username} signed in to {user.department}")

        return AuthService._issue_tokens(user)

    # ------------------------------------------------------------------
    # Tokens
    # ------------------------------------------------------------------
    @staticmethod
    def _issue_tokens(user: User) -> dict[str, Any]:
        claims = {
            "username": user.username,
            "user_id": user.id,
            "department": user.department,
            "role": user.role,
        }
        return {
            "access_token": create_access_token(**claims),
            "refresh_token": create_refresh_token(**claims),
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "user": user,
        }

    @staticmethod
    async def refresh(
        db: AsyncSession, refresh_token: str, *, audit: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = decode_token(refresh_token, expected_type="refresh")

        user = await UserRepository.get_by_id(db, payload.uid)
        if user is None or not user.is_active:
            raise TokenError("Account is no longer active.")

        # Re-check the department: a transfer between departments must
        # invalidate outstanding refresh tokens too.
        if user.department != payload.dept or user.role != payload.role:
            write_audit_event(
                "refresh_rejected_stale_claims",
                username=user.username,
                user_id=user.id,
                department=user.department,
                success=False,
                detail={"token_department": payload.dept},
                **(audit or {}),
            )
            raise TokenError("Your access profile has changed. Please sign in again.")

        return AuthService._issue_tokens(user)

    # ------------------------------------------------------------------
    # Password change
    # ------------------------------------------------------------------
    @staticmethod
    async def change_password(
        db: AsyncSession,
        user: User,
        *,
        current_password: str,
        new_password: str,
        audit: dict[str, Any] | None = None,
    ) -> None:
        if not verify_password(current_password, user.password_hash):
            write_audit_event(
                "password_change_failed",
                username=user.username,
                user_id=user.id,
                department=user.department,
                success=False,
                **(audit or {}),
            )
            raise AuthenticationError("Current password is incorrect.")

        if verify_password(new_password, user.password_hash):
            raise ValidationError("New password must differ from the current one.")

        await UserRepository.set_password_hash(db, user, hash_password(new_password))

        write_audit_event(
            "password_changed",
            username=user.username,
            user_id=user.id,
            department=user.department,
            **(audit or {}),
        )

    # ------------------------------------------------------------------
    # Departments
    # ------------------------------------------------------------------
    @staticmethod
    def list_departments() -> list[dict[str, str]]:
        from backend.core.constants import DEPARTMENT_SCOPE

        return [
            {"name": dept, "description": DEPARTMENT_SCOPE.get(dept, dept)}
            for dept in Department.values()
        ]
