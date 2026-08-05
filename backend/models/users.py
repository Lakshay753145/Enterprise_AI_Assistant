"""User model.

`department` is not a preference - it is this user's entire data universe. It
is stamped into the JWT at login and every downstream query is filtered by it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.constants import Department, Role
from backend.database.database import Base

if TYPE_CHECKING:
    from backend.models.chat import Conversation, Message
    from backend.models.documents import Document


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    department: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    role: Mapped[str] = mapped_column(
        String(30), nullable=False, default=Role.USER.value
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # --- Brute-force protection ---------------------------------------------
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # --- Relationships -------------------------------------------------------
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    uploaded_documents: Mapped[list["Document"]] = relationship(
        back_populates="uploader", foreign_keys="Document.uploaded_by_id"
    )

    __table_args__ = (
        CheckConstraint(
            "department IN ('" + "','".join(Department.values()) + "')",
            name="ck_users_department_valid",
        ),
        CheckConstraint(
            "role IN ('" + "','".join(Role.values()) + "')",
            name="ck_users_role_valid",
        ),
        Index("ix_users_department_active", "department", "is_active"),
    )

    # --- Derived state -------------------------------------------------------
    @property
    def is_locked(self) -> bool:
        if self.locked_until is None:
            return False
        locked_until = self.locked_until
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        return locked_until > datetime.now(timezone.utc)

    @property
    def is_admin(self) -> bool:
        return self.role in (Role.ADMIN.value, Role.SUPER_ADMIN.value)

    @property
    def is_super_admin(self) -> bool:
        return self.role == Role.SUPER_ADMIN.value

    @property
    def display_name(self) -> str:
        return self.full_name or self.username

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<User id={self.id} username={self.username!r} "
            f"department={self.department!r} role={self.role!r}>"
        )
