"""Security audit trail (database mirror of logs/audit/*.jsonl).

The JSONL files are the durable, tamper-evident record; this table exists so
the admin UI can query and chart the same events without parsing files.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    event: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    username: Mapped[str | None] = mapped_column(String(100), index=True)
    department: Mapped[str | None] = mapped_column(String(50), index=True)
    role: Mapped[str | None] = mapped_column(String(30))

    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)

    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        Index("ix_audit_event_created", "event", "created_at"),
        Index("ix_audit_department_created", "department", "created_at"),
        # Partial index: failures are what an auditor actually scans for.
        Index(
            "ix_audit_failures",
            "created_at",
            postgresql_where=text("success = false"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditLog {self.event} user={self.username} ok={self.success}>"
