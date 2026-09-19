"""Pydantic schemas for ingestion API requests, responses, and job status."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel


class Chunk(BaseModel):
    """A single chunk of parsed document text, with full provenance metadata."""

    chunk_id: str
    document_id: str
    chunk_index: int
    text: str
    section_path: list[str]
    page_start: int
    page_end: int
    char_count: int
    parser_used: Literal["fast", "quality"]
    source_filename: str


class IngestResponse(BaseModel):
    """The completed result of ingesting one document: its ID and resulting chunks.

    `parsing_confidence` (ERP-076) is a document-level label -- `"high"` for a document the
    fast path handled cleanly, or docling's own `"poor"`/`"fair"`/`"good"`/`"excellent"`/
    `"unspecified"` grade for one that needed the OCR fallback -- so a caller can tell at a
    glance whether a document (e.g. scanned, blurry, non-English) parsed reliably.
    """

    document_id: str
    chunks: list[Chunk]
    parsing_confidence: str


class DocumentSummary(BaseModel):
    """One of the caller's successfully ingested documents."""

    document_id: str
    filename: str
    created_at: datetime
    parsing_confidence: str


class DocumentListResponse(BaseModel):
    """The caller's successfully ingested documents, newest first."""

    documents: list[DocumentSummary]


class ChunkDetailResponse(BaseModel):
    """One chunk's full text and provenance, for the frontend's source panel (ERP-050)."""

    chunk_id: str
    document_id: str
    text: str
    section_path: list[str]
    page_start: int
    page_end: int
    source_filename: str


class JobStatus(str, Enum):
    """Lifecycle status of an async ingestion job."""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class JobStatusResponse(BaseModel):
    """Polled status of an ingestion job, with its result or error once finished."""

    status: JobStatus
    result: IngestResponse | None = None
    error: str | None = None
