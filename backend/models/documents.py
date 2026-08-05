"""Knowledge-base documents and their embedded chunks.

`DocumentChunk` carries a denormalised `department` column. That duplication is
intentional: it lets the RLS policy and the retrieval SQL filter on the chunk
row itself without a join, so there is no query shape in which a chunk can be
returned without its department having been checked.
"""

from __future__ import annotations

import uuid as uuid_pkg
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.config.config import settings
from backend.core.constants import Department, DocumentStatus
from backend.database.database import Base

if TYPE_CHECKING:
    from backend.models.users import User


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False, default=uuid_pkg.uuid4
    )

    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))

    department: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    uploaded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    uploaded_by_username: Mapped[str] = mapped_column(String(100), nullable=False)

    file_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    #: SHA-256 of the file bytes - used to reject re-uploads of identical files
    #: within a department without re-running the (expensive) Docling parse.
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DocumentStatus.PENDING.value, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Wall-clock seconds the Docling parse + chunk + embed pipeline took.
    processing_seconds: Mapped[float | None] = mapped_column()

    doc_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    uploader: Mapped["User | None"] = relationship(
        back_populates="uploaded_documents", foreign_keys=[uploaded_by_id]
    )
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        # The same file may legitimately exist in two departments; it may not
        # be ingested twice within one.
        UniqueConstraint("department", "file_hash", name="uq_documents_dept_hash"),
        CheckConstraint(
            "department IN ('" + "','".join(Department.values()) + "')",
            name="ck_documents_department_valid",
        ),
        Index("ix_documents_department_status", "department", "status"),
        Index("ix_documents_department_created", "department", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Document id={self.id} {self.original_filename!r} "
            f"dept={self.department!r} status={self.status!r}>"
        )


class DocumentChunk(Base):
    """One retrievable passage.

    Carries both halves of hybrid search:
      * `embedding`     - dense vector, queried with pgvector cosine distance
      * `content_tsv`   - Postgres full-text vector, queried with ts_rank_cd

    `content_tsv` is a GENERATED column so it can never drift from `content`.
    The heading is weighted 'A' and the body 'B', which makes a keyword hit in
    a section title outrank the same word buried in a paragraph.
    """

    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False, default=uuid_pkg.uuid4
    )

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: Denormalised from Document so RLS and retrieval never need a join.
    department: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: Section breadcrumb from Docling, e.g. "Quality Manual > 7.3 Inspection".
    heading: Mapped[str | None] = mapped_column(Text)
    section_path: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.EMBEDDING_DIMENSION), nullable=False
    )

    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(heading, '')), 'A') || "
            "setweight(to_tsvector('english', content), 'B')",
            persisted=True,
        ),
        nullable=False,
    )

    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunk_doc_index"),
        CheckConstraint(
            "department IN ('" + "','".join(Department.values()) + "')",
            name="ck_chunks_department_valid",
        ),
        # Composite index: every retrieval query starts with a department
        # equality predicate, so it must be the leading column.
        Index("ix_chunks_department_doc", "department", "document_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        preview = self.content[:60].replace("\n", " ")
        return (
            f"<DocumentChunk id={self.id} doc={self.document_id} "
            f"dept={self.department!r} {preview!r}...>"
        )
