# tests/ingestion/test_parsers.py
from app.ingestion.config import IngestionSettings
from app.ingestion.parsers import needs_fallback, parse_pdf


def _settings():
    return IngestionSettings(chunk_size=1500, chunk_overlap=200, ocr_text_threshold=20)


# --- needs_fallback: pure unit tests, no real parsing ---

def test_needs_fallback_true_for_low_text_page():
    fast_pages = [{"text": " ", "tables": [], "metadata": {"page_number": 1}}]
    assert needs_fallback(fast_pages, ocr_text_threshold=20) is True


def test_needs_fallback_true_when_table_detected():
    # Real pymupdf4llm (layout-analysis mode, the installed default) represents
    # detected regions as "page_boxes" entries with a "class" field, e.g. one
    # with class "table" for a detected table region.
    fast_pages = [
        {
            "text": "plenty of readable text here to pass the threshold check",
            "page_boxes": [{"index": 0, "class": "table", "bbox": (0, 0, 1, 1), "pos": (0, 10)}],
            "metadata": {"page_number": 1},
        }
    ]
    assert needs_fallback(fast_pages, ocr_text_threshold=20) is True


def test_needs_fallback_false_for_normal_text_page():
    fast_pages = [
        {
            "text": "plenty of readable text here to pass the threshold check",
            "page_boxes": [{"index": 0, "class": "section-header", "bbox": (0, 0, 1, 1), "pos": (0, 10)}],
            "metadata": {"page_number": 1},
        }
    ]
    assert needs_fallback(fast_pages, ocr_text_threshold=20) is False


# --- parse_pdf: real parsing against generated fixture PDFs ---

def test_parse_pdf_uses_fast_path_for_simple_text(simple_text_pdf):
    pages, parser_used, confidence = parse_pdf(simple_text_pdf, _settings())
    assert parser_used == "fast"
    assert confidence == "high"
    assert len(pages) == 1
    assert "introduction" in pages[0]["text"].lower()
    assert pages[0]["page_number"] == 1


def test_parse_pdf_falls_back_to_quality_for_table(table_pdf, monkeypatch):
    monkeypatch.setattr(
        "app.ingestion.parsers.call_docling_service",
        lambda pdf_path, settings: ([{"text": "R0C0 R0C1 R1C0 R1C1", "page_number": 1}], "good"),
    )
    pages, parser_used, confidence = parse_pdf(table_pdf, _settings())
    assert parser_used == "quality"
    assert confidence == "good"
    assert len(pages) >= 1


def test_parse_pdf_falls_back_to_quality_for_scanned_page(scanned_pdf, monkeypatch):
    monkeypatch.setattr(
        "app.ingestion.parsers.call_docling_service",
        lambda pdf_path, settings: ([{"text": "scanned content", "page_number": 1}], "poor"),
    )
    pages, parser_used, confidence = parse_pdf(scanned_pdf, _settings())
    assert parser_used == "quality"
    assert confidence == "poor"
