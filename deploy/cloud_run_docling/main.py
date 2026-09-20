"""Cloud Run service: runs docling's DocumentConverter, isolated from the main app's VM.

Not part of the main `app` package and not installed on the always-on VM -- this is the whole
point (docling's ~4GB documented baseline memory requirement vs. the VM's 958MB). See
docs/superpowers/specs/2026-09-17-docling-cloud-run-offload-design.md.
"""

import os
import tempfile
from typing import Any

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.settings import settings as docling_settings
from docling.document_converter import DocumentConverter, PdfFormatOption
from fastapi import FastAPI, HTTPException, UploadFile

_PAGE_BREAK = "\n\n<!-- docling-page-break -->\n\n"

# Models are pre-downloaded at Docker build time (`docling-tools models download`, baked into
# the image) to `settings.cache_dir / "models"` -- pointing the converter at that path
# explicitly makes it run fully offline. Without this, docling falls back to checking
# HuggingFace at request time even with a populated cache, which hit HF's rate limit in
# practice on Cloud Run's first real request.
_artifacts_path = docling_settings.cache_dir / "models"
_converter = DocumentConverter(
    format_options={
        InputFormat.PDF: PdfFormatOption(
            pipeline_options=PdfPipelineOptions(artifacts_path=_artifacts_path)
        )
    }
)

app = FastAPI(title="docling parsing service")


@app.post("/parse")
async def parse(file: UploadFile) -> dict[str, Any]:
    """Parse an uploaded PDF with docling, returning its pages plus a document-level confidence grade.

    ERP-076: `{"pages": [{"text", "page_number"}, ...], "confidence": "poor"|"fair"|"good"|
    "excellent"|"unspecified"}`. `confidence` is docling's own `mean_grade` -- an aggregate
    across the whole document, not a per-page breakdown -- so a caller can tell at a glance
    whether a document (e.g. a scanned, blurry, non-English form) parsed reliably.
    """
    contents = await file.read()

    # `delete=False` + an explicit close before `docling` reopens the path: a file still held
    # open by this process can't be reopened by another reader on Windows (irrelevant on Cloud
    # Run's Linux runtime, but this keeps local dev/testing working too, and is more portable
    # code regardless of platform).
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        tmp.write(contents)
        tmp.close()
        try:
            result = _converter.convert(tmp.name)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Failed to parse PDF: {exc}") from exc
    finally:
        os.unlink(tmp.name)

    markdown = result.document.export_to_markdown(page_break_placeholder=_PAGE_BREAK)
    pages = [
        {"text": text, "page_number": index + 1}
        for index, text in enumerate(markdown.split(_PAGE_BREAK))
    ]
    return {"pages": pages, "confidence": result.confidence.mean_grade.value}


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check. Unauthenticated at the Cloud Run ingress level is fine -- no data exposed."""
    return {"status": "ok"}
