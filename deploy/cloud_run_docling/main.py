"""Cloud Run service: runs docling's DocumentConverter, isolated from the main app's VM.

Not part of the main `app` package and not installed on the always-on VM -- this is the whole
point (docling's ~4GB documented baseline memory requirement vs. the VM's 958MB). See
docs/superpowers/specs/2026-09-17-docling-cloud-run-offload-design.md.
"""

import os
import tempfile
from typing import Any

from docling.document_converter import DocumentConverter
from fastapi import FastAPI, HTTPException, UploadFile

_PAGE_BREAK = "\n\n<!-- docling-page-break -->\n\n"

app = FastAPI(title="docling parsing service")


@app.post("/parse")
async def parse(file: UploadFile) -> list[dict[str, Any]]:
    """Parse an uploaded PDF with docling, returning `{"text", "page_number"}` per page."""
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
            converter = DocumentConverter()
            result = converter.convert(tmp.name)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Failed to parse PDF: {exc}") from exc
    finally:
        os.unlink(tmp.name)

    markdown = result.document.export_to_markdown(page_break_placeholder=_PAGE_BREAK)
    return [
        {"text": text, "page_number": index + 1}
        for index, text in enumerate(markdown.split(_PAGE_BREAK))
    ]


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check. Unauthenticated at the Cloud Run ingress level is fine -- no data exposed."""
    return {"status": "ok"}
