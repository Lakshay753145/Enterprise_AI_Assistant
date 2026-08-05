"""User data access.

The `users` table is intentionally NOT under RLS: authentication has to look up
a user before any department context exists. Isolation for users is enforced by
the explicit department filters in this module instead.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.config import settings
from backend.models.users import User


class UserRepository:
    @staticmethod
    async def get_by_id(db: AsyncSession, user_id: int) -> User | None:
        return (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_username(db: AsyncSession, username: str) -> User | None:
        return (
            await db.execute(
                select(User).where(func.lower(User.username) == username.strip().lower())
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_email(db: AsyncSession, email: str) -> User | None:
        return (
            await db.execute(
                select(User).where(func.lower(User.email) == email.strip().lower())
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_by_department(
        db: AsyncSession, department: str, *, include_inactive: bool = False
    ) -> list[User]:
        stmt = select(User).where(User.department == department)
        if not include_inactive:
            stmt = stmt.where(User.is_active.is_(True))
        result = await db.execute(stmt.order_by(User.username))
        return list(result.scalars().all())

    @staticmethod
    async def list_all(db: AsyncSession) -> list[User]:
        result = await db.execute(
            select(User).order_by(User.department, User.username)
        )
        return list(result.scalars().all())

    @staticmethod
    async def create(db: AsyncSession, user: User) -> User:
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    # -- login bookkeeping -------------------------------------------------
    @staticmethod
    async def record_successful_login(db: AsyncSession, user: User) -> None:
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = datetime.now(timezone.utc)
        await db.commit()

    @staticmethod
    async def record_failed_login(db: AsyncSession, user: User) -> bool:
        """Increment the failure counter; lock the account at the limit.

        Returns True when this failure caused a lockout.
        """
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1

        if user.failed_login_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            user.locked_until = datetime.now(timezone.utc) + timedelta(
                minutes=settings.LOGIN_LOCKOUT_MINUTES
            )
            user.failed_login_attempts = 0
            await db.commit()
            return True

        await db.commit()
        return False

    @staticmethod
    async def set_password_hash(db: AsyncSession, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        await db.commit()

    @staticmethod
    async def set_active(db: AsyncSession, user: User, is_active: bool) -> User:
        user.is_active = is_active
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def count_by_department(db: AsyncSession) -> dict[str, int]:
        result = await db.execute(
            select(User.department, func.count(User.id))
            .where(User.is_active.is_(True))
            .group_by(User.department)
        )
        return {row[0]: row[1] for row in result.all()}
