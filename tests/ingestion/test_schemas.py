import pytest
from pydantic import ValidationError

from app.ingestion.schemas import Chunk, IngestResponse, JobStatus, JobStatusResponse


def test_chunk_requires_all_fields():
    chunk = Chunk(
        chunk_id="doc1-0",
        document_id="doc1",
        chunk_index=0,
        text="hello world",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        char_count=11,
        parser_used="fast",
        source_filename="test.pdf",
    )
    assert chunk.parser_used == "fast"


def test_chunk_rejects_invalid_parser_used():
    with pytest.raises(ValidationError):
        Chunk(
            chunk_id="doc1-0",
            document_id="doc1",
            chunk_index=0,
            text="hello",
            section_path=[],
            page_start=1,
            page_end=1,
            char_count=5,
            parser_used="turbo",
            source_filename="test.pdf",
        )


def test_ingest_response_holds_chunks():
    chunk = Chunk(
        chunk_id="doc1-0", document_id="doc1", chunk_index=0, text="hi",
        section_path=[], page_start=1, page_end=1, char_count=2,
        parser_used="quality", source_filename="test.pdf",
    )
    response = IngestResponse(document_id="doc1", chunks=[chunk], parsing_confidence="high")
    assert response.chunks[0].chunk_id == "doc1-0"


def test_job_status_response_defaults():
    response = JobStatusResponse(status=JobStatus.PENDING)
    assert response.result is None
    assert response.error is None
