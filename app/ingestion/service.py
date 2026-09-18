"""The ingestion service: orchestrates parsing and chunking a PDF into `Chunk`s."""

import uuid

from app.core.db import get_session_factory
from app.ingestion.chunker import chunk_markdown
from app.ingestion.config import IngestionSettings
from app.ingestion.parsers import parse_pdf
from app.ingestion.repository import list_documents_for_owner
from app.ingestion.schemas import Chunk, DocumentListResponse, DocumentSummary, IngestResponse


def ingest_pdf(pdf_path: str, source_filename: str, settings: IngestionSettings) -> IngestResponse:
    """Parse and chunk a PDF at `pdf_path`, returning provenance-tagged chunks."""
    document_id = str(uuid.uuid4())
    pages, parser_used = parse_pdf(pdf_path, settings)
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

    return IngestResponse(document_id=document_id, chunks=chunks)


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
            DocumentSummary(document_id=r.document_id, filename=r.filename, created_at=r.created_at)
            for r in records
        ]
    )
