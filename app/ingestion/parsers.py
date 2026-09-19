"""PDF parsing: a fast native-text path with a slower quality (tables/OCR) fallback.

The quality fallback runs on a separate Cloud Run service (ERP-047), not in-process -- docling's
own documented ~4GB baseline memory requirement is far more than the live deployment's VM can
safely carry alongside the web app itself (a real incident: a document upload took the whole VM
unresponsive before this fix). See
docs/superpowers/specs/2026-09-17-docling-cloud-run-offload-design.md.
"""

from typing import Any, Literal, cast

import pymupdf4llm

from app.ingestion.cloud_run_client import call_docling_service
from app.ingestion.config import IngestionSettings

# ERP-076: the confidence label for a document that never needed the Docling fallback -- the
# fast path has no native confidence signal of its own, and a document that parsed cleanly on
# it needs no extra (costly) Docling call purely to score it.
FAST_PATH_CONFIDENCE = "high"


def parse_fast(pdf_path: str) -> list[dict[str, Any]]:
    """Raw PyMuPDF4LLM page_chunks output — used for both extraction and fallback routing."""
    return cast(list[dict[str, Any]], pymupdf4llm.to_markdown(pdf_path, page_chunks=True))


def needs_fallback(fast_pages: list[dict[str, Any]], ocr_text_threshold: int) -> bool:
    """Decide whether a document needs the quality (Cloud Run docling) parse instead of the fast path."""
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


def parse_quality(pdf_path: str, settings: IngestionSettings) -> tuple[list[dict[str, Any]], str]:
    """Quality parse (better tables + OCR) via the Cloud Run docling service (ERP-047).

    Returns `(pages, confidence)` -- `pages` are `{"text", "page_number"}` dicts, same shape as
    the fast path's normalized output; `confidence` is docling's document-level grade (ERP-076).
    """
    return call_docling_service(pdf_path, settings)


def parse_pdf(
    pdf_path: str, settings: IngestionSettings
) -> tuple[list[dict[str, Any]], Literal["fast", "quality"], str]:
    """Parse a PDF, using the fast path unless `needs_fallback` routes to the quality path.

    Returns `(pages, parser_used, parsing_confidence)` -- `parsing_confidence` (ERP-076) is
    `FAST_PATH_CONFIDENCE` for the fast path (which has no native confidence signal of its
    own) or docling's own document-level grade for the quality path.
    """
    fast_pages = parse_fast(pdf_path)

    if needs_fallback(fast_pages, settings.ocr_text_threshold):
        pages, confidence = parse_quality(pdf_path, settings)
        return pages, "quality", confidence

    normalized = [
        {"text": page["text"], "page_number": page["metadata"]["page_number"]}
        for page in fast_pages
    ]
    return normalized, "fast", FAST_PATH_CONFIDENCE
