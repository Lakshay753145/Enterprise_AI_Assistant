"""Document data access. Every query is department-scoped."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.constants import DocumentStatus
from backend.models.documents import Document, DocumentChunk


class DocumentRepository:
    @staticmethod
    async def get_by_id(
        db: AsyncSession, document_id: int, *, department: str
    ) -> Document | None:
        return (
            await db.execute(
                select(Document).where(
                    Document.id == document_id, Document.department == department
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_uuid(
        db: AsyncSession, document_uuid: UUID, *, department: str
    ) -> Document | None:
        return (
            await db.execute(
                select(Document).where(
                    Document.uuid == document_uuid, Document.department == department
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_hash(
        db: AsyncSession, file_hash: str, *, department: str
    ) -> Document | None:
        return (
            await db.execute(
                select(Document).where(
                    Document.file_hash == file_hash,
                    Document.department == department,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_by_department(
        db: AsyncSession,
        department: str,
        *,
        status: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Document]:
        stmt = select(Document).where(Document.department == department)
        if status:
            stmt = stmt.where(Document.status == status)
        result = await db.execute(
            stmt.order_by(desc(Document.created_at)).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    @staticmethod
    async def count_by_department(db: AsyncSession, department: str) -> int:
        return (
            await db.execute(
                select(func.count(Document.id)).where(
                    Document.department == department
                )
            )
        ).scalar_one()

    @staticmethod
    async def create(db: AsyncSession, document: Document) -> Document:
        db.add(document)
        await db.commit()
        await db.refresh(document)
        return document

    @staticmethod
    async def delete(db: AsyncSession, document: Document) -> None:
        """Delete a document; chunks go with it via ON DELETE CASCADE."""
        await db.delete(document)
        await db.commit()

    @staticmethod
    async def summary(db: AsyncSession, department: str) -> dict[str, Any]:
        row = (
            await db.execute(
                select(
                    func.count(Document.id),
                    func.coalesce(func.sum(Document.chunk_count), 0),
                    func.coalesce(func.sum(Document.page_count), 0),
                    func.max(Document.processed_at),
                ).where(Document.department == department)
            )
        ).one()

        status_rows = (
            await db.execute(
                select(Document.status, func.count(Document.id))
                .where(Document.department == department)
                .group_by(Document.status)
            )
        ).all()
        by_status = {status: count for status, count in status_rows}

        return {
            "department": department,
            "total_documents": row[0] or 0,
            "completed": by_status.get(DocumentStatus.COMPLETED.value, 0),
            "processing": by_status.get(DocumentStatus.PROCESSING.value, 0)
            + by_status.get(DocumentStatus.PENDING.value, 0),
            "failed": by_status.get(DocumentStatus.FAILED.value, 0),
            "total_chunks": int(row[1] or 0),
            "total_pages": int(row[2] or 0),
            "last_ingested_at": row[3],
        }

    @staticmethod
    async def chunk_count(db: AsyncSession, department: str) -> int:
        return (
            await db.execute(
                select(func.count(DocumentChunk.id)).where(
                    DocumentChunk.department == department
                )
            )
        ).scalar_one()

    @staticmethod
    async def has_any_content(db: AsyncSession, department: str) -> bool:
        """True when a department has at least one retrievable chunk.

        Used to give a much better error than "no results" when a department's
        knowledge base has simply not been ingested yet.
        """
        return (
            await DocumentRepository.chunk_count(db, department)
        ) > 0
