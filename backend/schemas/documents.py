"""Document management schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentResponse(BaseModel):
    id: int
    uuid: UUID
    filename: str
    original_filename: str
    title: str | None = None
    department: str
    uploaded_by_username: str
    file_type: str
    size_bytes: int
    status: str
    error_message: str | None = None
    page_count: int
    chunk_count: int
    processing_seconds: float | None = None
    doc_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    processed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int
    department: str


class UploadResponse(BaseModel):
    document: DocumentResponse
    message: str


class IngestionSummary(BaseModel):
    department: str
    total_documents: int
    completed: int
    processing: int
    failed: int
    total_chunks: int
    total_pages: int
    last_ingested_at: datetime | None = None


class ReindexRequest(BaseModel):
    #: Re-ingest even when the file hash already exists.
    replace_existing: bool = False
