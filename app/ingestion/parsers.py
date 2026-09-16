"""PDF parsing: a fast native-text path with a slower quality (tables/OCR) fallback."""

from typing import Any, Literal, cast

import pymupdf4llm

from app.ingestion.config import IngestionSettings

_PAGE_BREAK = "\n\n<!-- docling-page-break -->\n\n"


def parse_fast(pdf_path: str) -> list[dict[str, Any]]:
    """Raw PyMuPDF4LLM page_chunks output — used for both extraction and fallback routing."""
    return cast(list[dict[str, Any]], pymupdf4llm.to_markdown(pdf_path, page_chunks=True))


def needs_fallback(fast_pages: list[dict[str, Any]], ocr_text_threshold: int) -> bool:
    """Decide whether a document needs the quality (Docling) parse instead of the fast path."""
    for page in fast_pages:
        if len(page["text"].strip()) < ocr_text_threshold:
            return True
        # pymupdf4llm defaults to its layout-analysis engine (no config in this
        # codebase switches it to legacy/non-layout mode), whose page_chunks
        # output represents detected regions as a "page_boxes" list of dicts
        # with a "class" field (e.g. "table", "section-header") rather than a
        # dedicated "tables" key. Table presence is therefore detected here.
        if any(box.get("class") == "table" for box in (page.get("page_boxes") or [])):
            return True
    return False


def parse_quality(pdf_path: str) -> list[dict[str, Any]]:
    """Docling parse (quality path: better tables + OCR). Returns {"text", "page_number"} dicts.

    Imports `docling` lazily (not at module level) -- it pulls in `torch`/`transformers`, a
    heavy cost every deployment would otherwise pay at import time even if this fallback path
    (most documents use the fast path) is never actually reached.
    """
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(pdf_path)
    markdown = result.document.export_to_markdown(page_break_placeholder=_PAGE_BREAK)
    return [
        {"text": text, "page_number": index + 1}
        for index, text in enumerate(markdown.split(_PAGE_BREAK))
    ]


def parse_pdf(
    pdf_path: str, settings: IngestionSettings
) -> tuple[list[dict[str, Any]], Literal["fast", "quality"]]:
    """Parse a PDF, using the fast path unless `needs_fallback` routes to the quality path."""
    fast_pages = parse_fast(pdf_path)

    if needs_fallback(fast_pages, settings.ocr_text_threshold):
        return parse_quality(pdf_path), "quality"

    normalized = [
        {"text": page["text"], "page_number": page["metadata"]["page_number"]}
        for page in fast_pages
    ]
    return normalized, "fast"
