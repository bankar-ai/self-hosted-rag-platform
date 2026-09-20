"""The ingestion service: orchestrates parsing and chunking a PDF into `Chunk`s."""

import uuid

from app.core.db import get_session_factory
from app.ingestion.chunker import chunk_markdown
from app.ingestion.config import IngestionSettings
from app.ingestion.parsers import parse_pdf
from app.ingestion.repository import get_chunk_by_document_and_owner, list_documents_for_owner
from app.ingestion.schemas import (
    Chunk,
    ChunkDetailResponse,
    DocumentListResponse,
    DocumentSummary,
    IngestResponse,
)


def ingest_pdf(pdf_path: str, source_filename: str, settings: IngestionSettings) -> IngestResponse:
    """Parse and chunk a PDF at `pdf_path`, returning provenance-tagged chunks."""
    document_id = str(uuid.uuid4())
    pages, parser_used, parsing_confidence = parse_pdf(pdf_path, settings)
    raw_chunks = chunk_markdown(pages, settings)

    chunks = [
        Chunk(
            chunk_id=f"{document_id}-{index}",
            document_id=document_id,
            chunk_index=index,
            text=raw["text"],
            section_path=raw["section_path"],
            page_start=raw["page_start"],
            page_end=raw["page_end"],
            char_count=raw["char_count"],
            parser_used=parser_used,
            source_filename=source_filename,
        )
        for index, raw in enumerate(raw_chunks)
    ]

    return IngestResponse(
        document_id=document_id, chunks=chunks, parsing_confidence=parsing_confidence
    )


def list_documents(owner_id: uuid.UUID) -> DocumentListResponse:
    """Return `owner_id`'s successfully ingested documents, newest first.

    Only covers documents that finished ingestion (a `DocumentRecord` row is only created
    once `embed_and_persist` succeeds) -- a still-pending/processing/failed upload has no
    row here at all, and stays purely a client-tracked job until it either succeeds (and
    shows up in this list) or is dismissed client-side.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        records = list_documents_for_owner(session, owner_id)

    return DocumentListResponse(
        documents=[
            DocumentSummary(
                document_id=r.document_id,
                filename=r.filename,
                created_at=r.created_at,
                parsing_confidence=r.parsing_confidence,
            )
            for r in records
        ]
    )


def get_chunk_detail(document_id: str, chunk_id: str, owner_id: uuid.UUID) -> ChunkDetailResponse | None:
    """Return one chunk's full text for the source panel (ERP-050).

    `None` if the chunk is unknown, belongs to a different document, or the document isn't
    owned by `owner_id` -- the router maps this to a `404`.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        record = get_chunk_by_document_and_owner(session, document_id, chunk_id, owner_id)
        if record is None:
            return None
        return ChunkDetailResponse(
            chunk_id=record.chunk_id,
            document_id=record.document_id,
            text=record.text,
            section_path=record.section_path,
            page_start=record.page_start,
            page_end=record.page_end,
            source_filename=record.source_filename,
        )
