# tests/ingestion/test_service.py
from app.ingestion.config import IngestionSettings
from app.ingestion.service import ingest_pdf


def _settings():
    return IngestionSettings(chunk_size=1500, chunk_overlap=200, ocr_text_threshold=20)


def test_ingest_pdf_returns_chunks_with_full_provenance(simple_text_pdf):
    response = ingest_pdf(simple_text_pdf, "simple.pdf", _settings())

    assert response.document_id
    assert len(response.chunks) >= 1

    first = response.chunks[0]
    assert first.document_id == response.document_id
    assert first.chunk_id == f"{response.document_id}-0"
    assert first.chunk_index == 0
    assert first.source_filename == "simple.pdf"
    assert first.parser_used == "fast"
    assert first.page_start == 1


def test_ingest_pdf_chunk_indices_are_sequential(simple_text_pdf):
    response = ingest_pdf(simple_text_pdf, "simple.pdf", _settings())
    indices = [chunk.chunk_index for chunk in response.chunks]
    assert indices == list(range(len(response.chunks)))


def test_ingest_pdf_fast_path_gets_high_parsing_confidence(simple_text_pdf):
    """ERP-076: the fast path has no native confidence signal, so it defaults to "high"."""
    response = ingest_pdf(simple_text_pdf, "simple.pdf", _settings())
    assert response.parsing_confidence == "high"


def test_ingest_pdf_quality_path_carries_docling_confidence_through(table_pdf, monkeypatch):
    """ERP-076: a document routed to the Docling fallback gets its own document-level grade."""
    monkeypatch.setattr(
        "app.ingestion.parsers.call_docling_service",
        lambda pdf_path, settings: ([{"text": "R0C0 R0C1 R1C0 R1C1", "page_number": 1}], "fair"),
    )
    response = ingest_pdf(table_pdf, "table.pdf", _settings())
    assert response.parsing_confidence == "fair"
