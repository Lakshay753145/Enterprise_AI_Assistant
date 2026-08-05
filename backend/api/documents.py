"""Knowledge-base document management.

Uploads always land in the *uploader's own* department. There is no department
field on the request: accepting one would create an endpoint whose whole job is
to be tricked into writing into the wrong department.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    Request,
    UploadFile,
    status,
)

from fastapi.responses import FileResponse

from backend.config.config import settings
from backend.core.constants import API_PREFIX, DocumentStatus, SUPPORTED_EXTENSIONS
from backend.core.exceptions import (
    FileTooLargeError,
    NotFoundError,
    PermissionDeniedError,
    UnsupportedFileTypeError,
    ValidationError,
)
from backend.core.logging_config import app_logger, ingestion_logger, write_audit_event
from backend.models.documents import Document
from backend.repositories.document_repository import DocumentRepository
from backend.schemas.documents import (
    DocumentListResponse,
    DocumentResponse,
    IngestionSummary,
    UploadResponse,
)
from backend.security.dependencies import (
    AdminUser,
    CurrentUser,
    DbSession,
    audit_context,
    can_manage_documents,
)

router = APIRouter(prefix=f"{API_PREFIX}/documents", tags=["Documents"])


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List your department's documents",
)
async def list_documents(
    user: CurrentUser,
    db: DbSession,
    status_filter: str | None = None,
    limit: int = 200,
    offset: int = 0,
):
    documents = await DocumentRepository.list_by_department(
        db,
        user.department,
        status=status_filter,
        limit=min(limit, 500),
        offset=offset,
    )
    total = await DocumentRepository.count_by_department(db, user.department)
    return DocumentListResponse(
        documents=[DocumentResponse.model_validate(d) for d in documents],
        total=total,
        department=user.department,
    )


@router.get(
    "/summary",
    response_model=IngestionSummary,
    summary="Ingestion statistics for your department",
)
async def department_summary(user: CurrentUser, db: DbSession):
    return IngestionSummary(**await DocumentRepository.summary(db, user.department))


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document (administrators only)",
)
async def upload_document(
    request: Request,
    background: BackgroundTasks,
    user: AdminUser,
    db: DbSession,
    file: UploadFile = File(...),
) -> UploadResponse:
    """Accept a file, store it, and ingest it in the background.

    Returns 202 immediately with a `processing` document. Docling parsing of a
    large scanned PDF can take minutes; holding the HTTP connection open for
    that would time out at the proxy and give the admin no feedback.
    """
    if not file.filename:
        raise ValidationError("No filename supplied.")

    # IT Admin can upload into any department.
    # Other department admins are forced into their own department.
    target_department = user.department

    # Permission check – admins can only upload to their own department unless super-admin.
    if not can_manage_documents(user, target_department):
        raise PermissionDeniedError(
            "You can upload documents only to your own department."
        )

    suffix = Path(file.filename).suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"'{suffix}' is not supported. Allowed: "
            + ", ".join(sorted(SUPPORTED_EXTENSIONS))
        )

    # Ensure upload directory exists safely
    try:
        department_dir = settings.upload_path / target_department
        department_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        app_logger.error(f"Failed to create upload directory '{settings.upload_path / target_department}': {exc}")
        raise ValidationError("Server upload path is unaccessible or missing permissions.")

    safe_name = _safe_filename(file.filename)
    stored_name = f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}_{uuid4().hex[:8]}_{safe_name}"
    destination = department_dir / stored_name

    # Stream file to disk safely
    size = await _stream_to_disk(file, destination)

    # Import hash helper safely with exception handling
    try:
        from backend.ingestion.docling_parser import compute_file_hash
        file_hash = compute_file_hash(destination)
    except Exception as exc:
        app_logger.error(f"Failed to compute file hash or import docling parser: {exc}")
        destination.unlink(missing_ok=True)
        raise ValidationError("Failed to compute hash for the uploaded document.")

    existing = await DocumentRepository.get_by_hash(
        db, file_hash, department=target_department
    )
    if existing is not None and existing.status == DocumentStatus.COMPLETED.value:
        destination.unlink(missing_ok=True)
        return UploadResponse(
            document=DocumentResponse.model_validate(existing),
            message=(
                f"'{file.filename}' is already in the {target_department} knowledge "
                f"base (uploaded {existing.created_at:%d %b %Y}). Nothing to do."
            ),
        )

    document = await DocumentRepository.create(
        db,
        Document(
            filename=stored_name,
            original_filename=file.filename,
            department=target_department,
            uploaded_by_id=user.id,
            uploaded_by_username=user.username,
            file_type=file.content_type or "application/octet-stream",
            file_path=str(destination),
            file_hash=file_hash,
            size_bytes=size,
            status=DocumentStatus.PENDING.value,
        ),
    )

    write_audit_event(
        "document_uploaded",
        detail={
            "filename": file.filename,
            "document_id": document.id,
            "size_bytes": size,
        },
        **audit_context(request, user),
    )

    background.add_task(
        _ingest_in_background,
        document_id=document.id,
        file_path=str(destination),
        department=target_department,
        username=user.username,
        user_id=user.id,
    )

    return UploadResponse(
        document=DocumentResponse.model_validate(document),
        message=(
            f"'{file.filename}' uploaded and queued for processing. "
            f"It will become searchable once parsing completes."
        ),
    )


@router.get(
    "/{document_uuid}",
    response_model=DocumentResponse,
    summary="Get one document's details",
)
async def get_document(document_uuid: UUID, user: CurrentUser, db: DbSession):
    document = await DocumentRepository.get_by_uuid(
        db, document_uuid, department=user.department
    )
    if document is None:
        raise NotFoundError("Document not found.")
    return DocumentResponse.model_validate(document)


@router.get(
    "/{document_uuid}/download",
    summary="Download the original file",
    response_class=FileResponse,
)
async def download_document(document_uuid: UUID, user: CurrentUser, db: DbSession):
    document = await DocumentRepository.get_by_uuid(
        db, document_uuid, department=user.department
    )
    if document is None:
        raise NotFoundError("Document not found.")

    path = Path(document.file_path)
    if not path.is_file():
        raise NotFoundError("The stored file is missing from disk.")

    # Confirm the resolved path is still inside uploads/ - a stored path that
    # somehow contained traversal must not become an arbitrary file read.
    try:
        path.resolve().relative_to(settings.upload_path.resolve())
    except ValueError:
        app_logger.critical(
            f"Document #{document.id} path escapes the upload directory: {path}"
        )
        raise NotFoundError("Document not available.")

    return FileResponse(
        path,
        filename=document.original_filename,
        media_type=document.file_type or "application/octet-stream",
    )


@router.delete(
    "/{document_uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document and its indexed chunks (administrators only)",
)
async def delete_document(
    document_uuid: UUID, request: Request, user: AdminUser, db: DbSession
) -> None:
    document = await DocumentRepository.get_by_uuid(
        db, document_uuid, department=user.department
    )
    if document is None:
        raise NotFoundError("Document not found.")

    file_path = Path(document.file_path)
    filename = document.original_filename
    document_id = document.id

    await DocumentRepository.delete(db, document)

    # Remove the stored copy only after the row is gone, so a failure here
    # leaves an orphan file rather than an index entry pointing at nothing.
    try:
        file_path.unlink(missing_ok=True)
    except OSError as exc:
        app_logger.warning(f"Could not delete {file_path}: {exc}")

    write_audit_event(
        "document_deleted",
        detail={"filename": filename, "document_id": document_id},
        **audit_context(request, user),
    )


@router.post(
    "/{document_uuid}/reprocess",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-parse and re-index a document (administrators only)",
)
async def reprocess_document(
    document_uuid: UUID,
    request: Request,
    background: BackgroundTasks,
    user: AdminUser,
    db: DbSession,
):
    document = await DocumentRepository.get_by_uuid(
        db, document_uuid, department=user.department
    )
    if document is None:
        raise NotFoundError("Document not found.")

    if not Path(document.file_path).is_file():
        raise NotFoundError("The stored file is missing; re-upload it instead.")

    document.status = DocumentStatus.PENDING.value
    document.error_message = None
    await db.commit()
    await db.refresh(document)

    write_audit_event(
        "document_reprocess_requested",
        detail={"document_id": document.id, "filename": document.original_filename},
        **audit_context(request, user),
    )

    background.add_task(
        _ingest_in_background,
        document_id=document.id,
        file_path=document.file_path,
        department=user.department,
        username=user.username,
        user_id=user.id,
        replace_existing=True,
    )

    return UploadResponse(
        document=DocumentResponse.model_validate(document),
        message=f"'{document.original_filename}' queued for reprocessing.",
    )


# ---------------------------------------------------------------------------
# Background ingestion
# ---------------------------------------------------------------------------

async def _ingest_in_background(
    *,
    document_id: int,
    file_path: str,
    department: str,
    username: str,
    user_id: int,
    replace_existing: bool = False,
) -> None:
    """Run the (blocking) ingestion pipeline off the event loop."""
    from backend.ingestion.pipeline import ingest_file

    try:
        result = await asyncio.to_thread(
            ingest_file,
            Path(file_path),
            department=department,
            uploaded_by_username=username,
            uploaded_by_id=user_id,
            document_id=document_id,
            replace_existing=replace_existing,
        )
        ingestion_logger.info(
            f"Background ingestion finished: {result.filename} -> {result.status} "
            f"({result.chunk_count} chunks in {result.seconds}s)"
        )
    except Exception:
        ingestion_logger.exception(
            f"Background ingestion crashed for document #{document_id}"
        )


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(filename: str) -> str:
    """Strip path components and anything that is not filename-safe."""
    name = Path(filename).name
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = _UNSAFE_CHARS.sub("_", name).strip("._") or "document"
    return name[:180]


async def _stream_to_disk(file: UploadFile, destination: Path) -> int:
    """Write the upload in chunks, enforcing the size cap as we go.

    Reading the whole body first to check its size would mean a 2 GB upload
    costs 2 GB of RAM before being rejected.
    """
    limit = getattr(settings, "max_upload_bytes", getattr(settings, "MAX_UPLOAD_BYTES", 50 * 1024 * 1024))
    max_mb = getattr(settings, "max_upload_size_mb", getattr(settings, "MAX_UPLOAD_SIZE_MB", 50))
    written = 0

    try:
        with destination.open("wb") as sink:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    sink.close()
                    destination.unlink(missing_ok=True)
                    raise FileTooLargeError(
                        f"File exceeds the {max_mb} MB limit."
                    )
                sink.write(chunk)
    except FileTooLargeError:
        raise
    except Exception as exc:
        app_logger.error(f"Error writing upload stream to disk at '{destination}': {exc}")
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    if written == 0:
        destination.unlink(missing_ok=True)
        raise ValidationError("The uploaded file is empty.")

    return written