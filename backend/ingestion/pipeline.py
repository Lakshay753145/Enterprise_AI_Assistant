"""Ingestion pipeline: file -> Docling -> chunks -> embeddings -> Postgres.

Deliberately synchronous. Docling and sentence-transformers are both blocking
CPU/GPU work, so pretending otherwise buys nothing. The API calls this from a
background task via ``asyncio.to_thread``; the CLI calls it directly.

Every chunk is written with the department stamped on it. The department comes
from the caller's authenticated context, never from the file or its path, so a
mis-filed PDF cannot smuggle itself into another department's index.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.config.config import settings
from backend.core.constants import Department, DocumentStatus, Role
from backend.core.exceptions import IngestionError, ValidationError
from backend.core.logging_config import ingestion_logger, write_audit_event
from backend.database.database import apply_rls_context_sync, get_sync_session_factory
from backend.embeddings.embedder import get_embedder
from backend.ingestion.chunker import chunk_document
from backend.ingestion.docling_parser import compute_file_hash, parse_document
from backend.models.documents import Document, DocumentChunk


@dataclass
class IngestionResult:
    document_id: int | None
    filename: str
    department: str
    status: str
    chunk_count: int = 0
    page_count: int = 0
    seconds: float = 0.0
    skipped_reason: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == DocumentStatus.COMPLETED.value


def ingest_file(
    file_path: Path | str,
    *,
    department: str,
    uploaded_by_username: str,
    uploaded_by_id: int | None = None,
    session: Session | None = None,
    replace_existing: bool = False,
    document_id: int | None = None,
) -> IngestionResult:
    """Ingest one file into a department's knowledge base.

    Args:
        document_id: when set, update this existing row (the API creates the
            row up front so the UI can show "processing" immediately).
        replace_existing: re-ingest even if an identical file hash is already
            present in this department.
    """
    path = Path(file_path)
    started = time.perf_counter()

    if not Department.is_valid(department):
        raise ValidationError(
            f"Unknown department {department!r}. Valid: "
            + ", ".join(Department.values())
        )

    owns_session = session is None
    if owns_session:
        session = get_sync_session_factory()()

    try:
        # Ingestion writes to RLS-protected tables, so the transaction needs a
        # department context just like an API request does.
        apply_rls_context_sync(session, department=department, role=Role.ADMIN.value)

        file_hash = compute_file_hash(path)

        existing = session.execute(
            select(Document).where(
                Document.department == department,
                Document.file_hash == file_hash,
                Document.status == DocumentStatus.COMPLETED.value,
            )
        ).scalar_one_or_none()

        if existing is not None and existing.id != document_id:
            if not replace_existing:
                ingestion_logger.info(
                    f"Skipping {path.name}: identical file already ingested into "
                    f"{department} as document #{existing.id}"
                )
                return IngestionResult(
                    document_id=existing.id,
                    filename=path.name,
                    department=department,
                    status="skipped",
                    chunk_count=existing.chunk_count,
                    page_count=existing.page_count,
                    seconds=round(time.perf_counter() - started, 2),
                    skipped_reason="duplicate_file_hash",
                )
            _purge_document(session, existing)

        document = _get_or_create_document(
            session,
            document_id=document_id,
            path=path,
            department=department,
            file_hash=file_hash,
            uploaded_by_username=uploaded_by_username,
            uploaded_by_id=uploaded_by_id,
        )

        document.status = DocumentStatus.PROCESSING.value
        document.error_message = None
        session.commit()

        try:
            result = _process(session, document, path)
        except Exception as exc:
            session.rollback()
            apply_rls_context_sync(
                session, department=department, role=Role.ADMIN.value
            )
            document = session.get(Document, document.id)
            if document is not None:
                document.status = DocumentStatus.FAILED.value
                document.error_message = str(exc)[:2000]
                session.commit()

            ingestion_logger.exception(f"Ingestion failed for {path.name}")
            write_audit_event(
                "document_ingestion_failed",
                username=uploaded_by_username,
                user_id=uploaded_by_id,
                department=department,
                success=False,
                detail={"filename": path.name, "error": str(exc)[:500]},
            )
            return IngestionResult(
                document_id=document.id if document else None,
                filename=path.name,
                department=department,
                status=DocumentStatus.FAILED.value,
                seconds=round(time.perf_counter() - started, 2),
                error=str(exc),
            )

        write_audit_event(
            "document_ingested",
            username=uploaded_by_username,
            user_id=uploaded_by_id,
            department=department,
            detail={
                "filename": path.name,
                "document_id": result.document_id,
                "chunks": result.chunk_count,
                "pages": result.page_count,
                "seconds": result.seconds,
            },
        )
        return result

    finally:
        if owns_session and session is not None:
            session.close()


def _get_or_create_document(
    session: Session,
    *,
    document_id: int | None,
    path: Path,
    department: str,
    file_hash: str,
    uploaded_by_username: str,
    uploaded_by_id: int | None,
) -> Document:
    if document_id is not None:
        document = session.get(Document, document_id)
        if document is None:
            raise IngestionError(f"Document #{document_id} not found.")
        if document.department != department:
            # Refuse to re-point a document at a different department.
            raise IngestionError(
                "Document department mismatch - refusing to ingest."
            )
        document.file_hash = file_hash
        document.size_bytes = path.stat().st_size
        return document

    document = Document(
        filename=path.name,
        original_filename=path.name,
        department=department,
        uploaded_by_id=uploaded_by_id,
        uploaded_by_username=uploaded_by_username,
        file_type=_guess_mime(path),
        file_path=str(path),
        file_hash=file_hash,
        size_bytes=path.stat().st_size,
        status=DocumentStatus.PENDING.value,
    )
    session.add(document)
    session.flush()
    return document


def _process(session: Session, document: Document, path: Path) -> IngestionResult:
    """Parse, chunk, embed, and persist. Assumes the document row exists."""
    started = time.perf_counter()

    parsed = parse_document(path)

    chunks = chunk_document(parsed)
    if not chunks:
        raise IngestionError(
            f"{path.name} yielded no usable chunks after filtering. The file may "
            f"be empty, image-only without OCR text, or entirely boilerplate."
        )

    embed_started = time.perf_counter()
    embedder = get_embedder()
    vectors = embedder.embed_documents([c.content for c in chunks])
    embed_seconds = time.perf_counter() - embed_started

    if len(vectors) != len(chunks):
        raise IngestionError(
            f"Embedding count mismatch: {len(vectors)} vectors for "
            f"{len(chunks)} chunks."
        )

    # Replace rather than append, so re-ingesting a corrected document does not
    # leave the superseded text retrievable.
    session.execute(
        delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
    )

    session.add_all(
        [
            DocumentChunk(
                document_id=document.id,
                department=document.department,
                chunk_index=chunk.index,
                content=chunk.content,
                heading=chunk.heading,
                section_path=chunk.section_path,
                page_number=chunk.page_number,
                token_count=chunk.token_count,
                embedding=vector,
                chunk_metadata={
                    **chunk.metadata,
                    "raw_content": chunk.raw_content[:4000],
                },
            )
            for chunk, vector in zip(chunks, vectors)
        ]
    )

    total_seconds = time.perf_counter() - started

    document.title = parsed.title
    document.page_count = parsed.page_count
    document.chunk_count = len(chunks)
    document.status = DocumentStatus.COMPLETED.value
    document.processed_at = datetime.now(timezone.utc)
    document.processing_seconds = round(total_seconds, 3)
    document.doc_metadata = {
        **parsed.metadata,
        "parse_seconds": parsed.parse_seconds,
        "embed_seconds": round(embed_seconds, 3),
        "char_count": parsed.char_count,
        "embedding_model": settings.EMBEDDING_MODEL,
        "chunker": chunks[0].metadata.get("chunker", "unknown"),
        "avg_tokens_per_chunk": round(
            sum(c.token_count for c in chunks) / len(chunks), 1
        ),
    }

    session.commit()

    ingestion_logger.info(
        f"Ingested {path.name} into {document.department}: {len(chunks)} chunks, "
        f"{parsed.page_count} pages in {total_seconds:.1f}s "
        f"(parse {parsed.parse_seconds:.1f}s, embed {embed_seconds:.1f}s)"
    )

    return IngestionResult(
        document_id=document.id,
        filename=path.name,
        department=document.department,
        status=DocumentStatus.COMPLETED.value,
        chunk_count=len(chunks),
        page_count=parsed.page_count,
        seconds=round(total_seconds, 2),
    )


def _purge_document(session: Session, document: Document) -> None:
    """Delete a document and its chunks (cascade handles the chunks)."""
    ingestion_logger.info(
        f"Replacing existing document #{document.id} ({document.original_filename})"
    )
    session.delete(document)
    session.commit()


def _guess_mime(path: Path) -> str:
    import mimetypes

    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


# ---------------------------------------------------------------------------
# Bulk ingestion of the knowledge_base/ tree
# ---------------------------------------------------------------------------

def ingest_department_folder(
    department: str,
    *,
    uploaded_by_username: str = "system",
    replace_existing: bool = False,
) -> list[IngestionResult]:
    """Ingest every supported file in ``knowledge_base/<Department>/``."""
    from backend.core.constants import SUPPORTED_EXTENSIONS

    folder = settings.knowledge_base_path / department
    if not folder.is_dir():
        ingestion_logger.warning(f"No knowledge base folder for {department}: {folder}")
        return []

    files = sorted(
        p
        for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    if not files:
        ingestion_logger.warning(f"No ingestible files found in {folder}")
        return []

    ingestion_logger.info(f"Ingesting {len(files)} file(s) for {department}")

    results: list[IngestionResult] = []
    factory = get_sync_session_factory()

    for path in files:
        session = factory()
        try:
            # Copy into uploads/<Department>/ so the canonical stored copy is
            # never the same file an admin might later move or delete.
            stored = _store_copy(path, department)
            results.append(
                ingest_file(
                    stored,
                    department=department,
                    uploaded_by_username=uploaded_by_username,
                    session=session,
                    replace_existing=replace_existing,
                )
            )
        except Exception as exc:
            ingestion_logger.exception(f"Failed on {path.name}")
            results.append(
                IngestionResult(
                    document_id=None,
                    filename=path.name,
                    department=department,
                    status=DocumentStatus.FAILED.value,
                    error=str(exc),
                )
            )
        finally:
            session.close()

    return results


def _store_copy(source: Path, department: str) -> Path:
    """Copy a knowledge_base file into uploads/<Department>/, if not already there."""
    target_dir = settings.upload_path / department
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name

    if target.resolve() == source.resolve():
        return target
    if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
        shutil.copy2(source, target)
    return target


def ingest_all_departments(
    *, uploaded_by_username: str = "system", replace_existing: bool = False
) -> dict[str, list[IngestionResult]]:
    return {
        dept: ingest_department_folder(
            dept,
            uploaded_by_username=uploaded_by_username,
            replace_existing=replace_existing,
        )
        for dept in Department.values()
    }
