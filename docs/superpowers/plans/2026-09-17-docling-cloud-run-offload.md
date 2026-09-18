# Docling Cloud Run Offload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `docling`'s PDF-parsing fallback off the always-on production VM onto a separate Google Cloud Run service, fixing a real outage (a document upload took the whole VM unresponsive) without losing `docling`'s extraction quality.

**Architecture:** A new standalone Cloud Run service (`deploy/cloud_run_docling/`) wraps `docling`'s `DocumentConverter` behind one HTTP endpoint. `app/ingestion/parsers.py`'s `parse_quality()` becomes a thin client that calls that service (via a new `app/ingestion/cloud_run_client.py`) instead of importing `docling` in-process. `docling`/`torch`/`transformers` are removed from the VM's own dependencies entirely. Auth is GCP-native IAM (the VM's service account fetches a short-lived identity token from the metadata server) — no stored secrets.

**Tech Stack:** FastAPI (both the main app and the new Cloud Run service), `httpx` (already a dependency, used for the metadata-server call and the service call — no new dependency), `docling` (moves to the new service's own `requirements.txt`, removed from the root `pyproject.toml`).

**Spec:** `docs/superpowers/specs/2026-09-17-docling-cloud-run-offload-design.md`

## Global Constraints

- No new Python dependency in the main project (`httpx` already covers the HTTP/auth needs).
- `docling`, and its transitive `torch`/`transformers` pull, must be fully removed from the root `pyproject.toml` and `uv.lock` — this is the actual point of the fix.
- The Cloud Run service's own dependencies are isolated in `deploy/cloud_run_docling/requirements.txt`, never merged into the root project.
- Cloud Run auth is IAM-only (`--no-allow-unauthenticated`, granted to the VM's service account) — never a stored shared secret.
- `docling_service_url` deviates slightly from the spec's "no default" language: made `str | None = None` at the `IngestionSettings` level (not hard-required at settings-construction time) so the 6+ existing test files that hand-construct `IngestionSettings(...)` with partial kwargs don't all need updating. `call_docling_service()` itself raises a clear error if it's unset and actually invoked — same "unconfigured = the feature is off" pattern this codebase already uses for OIDC's four optional settings.
- Backend changes must keep `ruff`/`mypy --strict`/`pytest-cov --cov-fail-under=90` green.

---

### Task 1: `IngestionSettings` gains the Cloud Run service config

**Files:**
- Modify: `app/ingestion/config.py`
- Test: `tests/ingestion/test_config.py`

**Interfaces:**
- Produces: `IngestionSettings.docling_service_url: str | None`, `IngestionSettings.docling_service_timeout_seconds: float`

- [ ] **Step 1: Write the failing test**

Read `tests/ingestion/test_config.py` first to match its existing style, then add:

```python
def test_docling_service_url_defaults_to_none():
    settings = IngestionSettings()
    assert settings.docling_service_url is None


def test_docling_service_timeout_defaults_to_480_seconds():
    settings = IngestionSettings()
    assert settings.docling_service_timeout_seconds == 480.0


def test_docling_service_url_overridable_via_env(monkeypatch):
    monkeypatch.setenv("INGESTION_DOCLING_SERVICE_URL", "https://example.run.app")
    settings = IngestionSettings()
    assert settings.docling_service_url == "https://example.run.app"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AUTH_JWT_SECRET_KEY=test uv run pytest tests/ingestion/test_config.py -v -k docling_service`
Expected: FAIL — `AttributeError` or similar, the field doesn't exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# app/ingestion/config.py -- add these two fields inside IngestionSettings, after max_upload_size_bytes
    docling_service_url: str | None = None
    docling_service_timeout_seconds: float = 480.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AUTH_JWT_SECRET_KEY=test uv run pytest tests/ingestion/test_config.py -v -k docling_service`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/config.py tests/ingestion/test_config.py
git commit -m "feat: add docling Cloud Run service settings (ERP-047)"
```

---

### Task 2: `app/ingestion/cloud_run_client.py` — the HTTP client

**Files:**
- Create: `app/ingestion/cloud_run_client.py`
- Test: `tests/ingestion/test_cloud_run_client.py`

**Interfaces:**
- Consumes: `IngestionSettings.docling_service_url`/`docling_service_timeout_seconds` (Task 1)
- Produces: `fetch_identity_token(audience: str) -> str`, `call_docling_service(pdf_path: str, settings: IngestionSettings) -> list[dict[str, Any]]`, `DoclingServiceError(Exception)` — consumed by Task 4's `parse_quality()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/ingestion/test_cloud_run_client.py
from unittest.mock import patch

import httpx
import pytest

from app.ingestion.cloud_run_client import (
    DoclingServiceError,
    call_docling_service,
    fetch_identity_token,
)
from app.ingestion.config import IngestionSettings

_RealHttpxClient = httpx.Client


def _stub_httpx_client(handler):
    """Fake all `httpx.Client(...)` calls (both the metadata-server GET and the service POST)
    through `handler`, mirroring `tests/auth/test_oidc.py`'s existing pattern for this repo."""
    return patch(
        "httpx.Client", lambda **kw: _RealHttpxClient(transport=httpx.MockTransport(handler), **kw)
    )


def _settings(**overrides):
    defaults = {
        "chunk_size": 1500,
        "chunk_overlap": 200,
        "ocr_text_threshold": 20,
        "docling_service_url": "https://docling-service.example.run.app",
        "docling_service_timeout_seconds": 5.0,
    }
    defaults.update(overrides)
    return IngestionSettings(**defaults)


def test_fetch_identity_token_requests_metadata_server_with_audience():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Metadata-Flavor"] == "Google"
        assert request.url.params["audience"] == "https://docling-service.example.run.app"
        return httpx.Response(200, text="fake-identity-token")

    with _stub_httpx_client(handler):
        token = fetch_identity_token("https://docling-service.example.run.app")

    assert token == "fake-identity-token"


def test_call_docling_service_posts_pdf_and_returns_parsed_pages(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-fake-content")

    def handler(request: httpx.Request) -> httpx.Response:
        if "identity" in str(request.url):
            return httpx.Response(200, text="fake-token")
        assert request.headers["Authorization"] == "Bearer fake-token"
        return httpx.Response(200, json=[{"text": "hello", "page_number": 1}])

    with _stub_httpx_client(handler):
        result = call_docling_service(str(pdf_path), _settings())

    assert result == [{"text": "hello", "page_number": 1}]


def test_call_docling_service_raises_on_non_200_response(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-fake-content")

    def handler(request: httpx.Request) -> httpx.Response:
        if "identity" in str(request.url):
            return httpx.Response(200, text="fake-token")
        return httpx.Response(500, text="internal error")

    with _stub_httpx_client(handler):
        with pytest.raises(DoclingServiceError):
            call_docling_service(str(pdf_path), _settings())


def test_call_docling_service_raises_when_url_not_configured(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-fake-content")

    with pytest.raises(DoclingServiceError):
        call_docling_service(str(pdf_path), _settings(docling_service_url=None))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `AUTH_JWT_SECRET_KEY=test uv run pytest tests/ingestion/test_cloud_run_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingestion.cloud_run_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/ingestion/cloud_run_client.py
"""HTTP client for the standalone Cloud Run docling-parsing service (ERP-047).

`docling` itself is never imported here or anywhere on the always-on VM -- its own documented
~4GB baseline memory requirement is why it moved to a separate service in the first place. See
docs/superpowers/specs/2026-09-17-docling-cloud-run-offload-design.md.
"""

import logging
from pathlib import Path
from typing import Any

import httpx

from app.ingestion.config import IngestionSettings

logger = logging.getLogger(__name__)

_METADATA_IDENTITY_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
)


class DoclingServiceError(Exception):
    """Raised when the Cloud Run docling service is unconfigured, unreachable, or errors."""


def fetch_identity_token(audience: str) -> str:
    """Fetch a short-lived GCP identity token scoped to `audience` from the metadata server.

    Only works when actually running on GCP (the VM) -- there is no local/CI fallback, and none
    is needed: tests fake the HTTP layer entirely rather than requiring a real metadata server.
    """
    with httpx.Client(timeout=5.0) as client:
        response = client.get(
            _METADATA_IDENTITY_URL,
            params={"audience": audience},
            headers={"Metadata-Flavor": "Google"},
        )
        response.raise_for_status()
        return response.text


def call_docling_service(pdf_path: str, settings: IngestionSettings) -> list[dict[str, Any]]:
    """Send the PDF at `pdf_path` to the Cloud Run docling service, returning its parsed pages.

    Raises `DoclingServiceError` if `settings.docling_service_url` isn't configured, the request
    times out, or the service returns a non-2xx response.
    """
    if not settings.docling_service_url:
        raise DoclingServiceError("INGESTION_DOCLING_SERVICE_URL is not configured")

    try:
        token = fetch_identity_token(settings.docling_service_url)
        pdf_bytes = Path(pdf_path).read_bytes()
        with httpx.Client(timeout=settings.docling_service_timeout_seconds) as client:
            response = client.post(
                f"{settings.docling_service_url}/parse",
                files={"file": (Path(pdf_path).name, pdf_bytes, "application/pdf")},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            result: list[dict[str, Any]] = response.json()
            return result
    except httpx.HTTPError as exc:
        logger.exception("Docling Cloud Run service call failed")
        raise DoclingServiceError(str(exc)) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `AUTH_JWT_SECRET_KEY=test uv run pytest tests/ingestion/test_cloud_run_client.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Run ruff and mypy**

Run: `uv run ruff check app/ingestion/cloud_run_client.py tests/ingestion/test_cloud_run_client.py && uv run mypy app/ingestion/cloud_run_client.py`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/cloud_run_client.py tests/ingestion/test_cloud_run_client.py
git commit -m "feat: add Cloud Run docling service HTTP client (ERP-047)"
```

---

### Task 3: The Cloud Run service itself

**Files:**
- Create: `deploy/cloud_run_docling/main.py`, `deploy/cloud_run_docling/requirements.txt`, `deploy/cloud_run_docling/Dockerfile`, `deploy/cloud_run_docling/README.md`
- Test: `deploy/cloud_run_docling/test_main.py` (run manually/locally, **not** wired into the root project's `uv run pytest` or CI — `docling` is intentionally not a root-project dependency any more; this mirrors `deploy/modal_ollama.py`, which also has no CI coverage and is verified by live testing instead)

**Interfaces:**
- Produces: a `POST /parse` HTTP endpoint (multipart `file` field, returns
  `list[{"text": str, "page_number": int}]` as JSON) and a `GET /health` endpoint — this is the
  contract `app/ingestion/cloud_run_client.py` (Task 2) already assumes.

- [ ] **Step 1: Write the service**

```python
# deploy/cloud_run_docling/main.py
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
# explicitly makes it run fully offline. Found live: without this, docling still checks
# HuggingFace at request time even with a populated cache, which hit HF's rate limit on Cloud
# Run's first real request and failed the whole job.
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
async def parse(file: UploadFile) -> list[dict[str, Any]]:
    """Parse an uploaded PDF with docling, returning `{"text", "page_number"}` per page."""
    contents = await file.read()

    # `delete=False` + an explicit close before `docling` reopens the path: a file still held
    # open by this process can't be reopened by another reader on Windows (irrelevant on Cloud
    # Run's Linux runtime, but this keeps local dev/testing working too, and is more portable
    # code regardless of platform). Found via a real local smoke test during implementation --
    # the naive `with tempfile.NamedTemporaryFile() as tmp:` form fails with a Windows
    # PermissionError the moment docling tries to reopen the still-open path.
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
    return [
        {"text": text, "page_number": index + 1}
        for index, text in enumerate(markdown.split(_PAGE_BREAK))
    ]


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check. Unauthenticated at the Cloud Run ingress level is fine -- no data exposed."""
    return {"status": "ok"}
```

```
# deploy/cloud_run_docling/requirements.txt
docling>=2.117.0
fastapi>=0.141.1
uvicorn>=0.52.0
python-multipart>=0.0.32
```

```dockerfile
# deploy/cloud_run_docling/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# opencv-python (pulled in transitively by docling's rapidocr OCR backend) needs these X11/GL
# shared libraries at import time -- python:3.12-slim's minimal base doesn't include them.
# Found live: the build failed with "ImportError: libxcb.so.1: cannot open shared object file"
# the first time this image tried to actually import cv2.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libxcb1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download docling's model weights at build time -- baked into the image layer, not
# fetched from HuggingFace at request time (which hit HF's rate limit in practice on cold
# start, causing the first real request to fail outright rather than just being slow).
RUN docling-tools models download

COPY main.py .

ENV PORT=8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
```

```markdown
# deploy/cloud_run_docling/README.md
Standalone Cloud Run service running `docling`'s `DocumentConverter`, isolated from the main
app's VM. See `docs/superpowers/specs/2026-09-17-docling-cloud-run-offload-design.md` (ERP-047).

## Local testing

```
pip install -r requirements.txt
pytest test_main.py
```

Not run in the main project's CI/`uv run pytest` -- `docling` is deliberately not a root-project
dependency any more (that's the whole point of this service existing).

## Deploy

```
gcloud run deploy self-hosted-rag-platform-docling \
  --source deploy/cloud_run_docling \
  --region us-central1 \
  --no-allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --timeout 600 \
  --concurrency 1 \
  --min-instances 0 \
  --max-instances 3 \
  --project self-hosted-rag-platform
```

`--concurrency 1` matters: `docling` is CPU/memory-heavy per request, and letting Cloud Run route
multiple concurrent requests into one container would let them compete for the same resources
that are already tightly budgeted. `--max-instances 3` bounds worst-case cost.

Then grant the VM's service account permission to call it (see `docs/deployment.md`'s docling
section for the exact command and how to find the VM's service account email).
```

- [ ] **Step 2: Write a local smoke test**

```python
# deploy/cloud_run_docling/test_main.py
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 3: Run the smoke test locally**

Run (from `deploy/cloud_run_docling/`, in a throwaway venv):
```
pip install -r requirements.txt
pytest test_main.py -v
```
Expected: PASS (1 passed). This installs `docling` temporarily to verify the service's own code
is syntactically correct and boots — it does not touch the main project's `.venv` or `uv.lock`.

- [ ] **Step 4: Commit**

```bash
git add deploy/cloud_run_docling/
git commit -m "feat: add standalone Cloud Run docling parsing service (ERP-047)"
```

---

### Task 4: Wire `parse_quality()` to the new client, remove `docling` from the root project

**Files:**
- Modify: `app/ingestion/parsers.py`
- Modify: `pyproject.toml` (remove the `docling` dependency)
- Modify: `tests/ingestion/test_parsers.py` (update the two fallback tests to mock the new client)

**Interfaces:**
- Consumes: `app.ingestion.cloud_run_client.call_docling_service` (Task 2)

- [ ] **Step 1: Update the two existing fallback tests to mock the Cloud Run call**

```python
# tests/ingestion/test_parsers.py -- replace both quality-fallback tests
def test_parse_pdf_falls_back_to_quality_for_table(table_pdf, monkeypatch):
    monkeypatch.setattr(
        "app.ingestion.parsers.call_docling_service",
        lambda pdf_path, settings: [{"text": "R0C0 R0C1 R1C0 R1C1", "page_number": 1}],
    )
    pages, parser_used = parse_pdf(table_pdf, _settings())
    assert parser_used == "quality"
    assert len(pages) >= 1


def test_parse_pdf_falls_back_to_quality_for_scanned_page(scanned_pdf, monkeypatch):
    monkeypatch.setattr(
        "app.ingestion.parsers.call_docling_service",
        lambda pdf_path, settings: [{"text": "scanned content", "page_number": 1}],
    )
    pages, parser_used = parse_pdf(scanned_pdf, _settings())
    assert parser_used == "quality"
```

(These replace the current bodies of both tests, which call real `docling` today. Everything
else in the file — `_settings()`, `needs_fallback` tests, the fast-path test — stays unchanged.)

- [ ] **Step 2: Run the tests to verify they fail (still importing real `docling`)**

Run: `AUTH_JWT_SECRET_KEY=test uv run pytest tests/ingestion/test_parsers.py -v`
Expected: the two updated tests still PASS at this point (monkeypatch targets a name that
doesn't exist in `app.ingestion.parsers` yet, so this actually raises `AttributeError` from
`monkeypatch.setattr` — confirming the test is exercising something real, not a no-op). If it's
green already, `parsers.py` hasn't been touched yet, which is correct at this step.

- [ ] **Step 3: Rewrite `parse_quality()` and update `parse_pdf()`'s call site**

```python
# app/ingestion/parsers.py -- full replacement
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


def parse_quality(pdf_path: str, settings: IngestionSettings) -> list[dict[str, Any]]:
    """Quality parse (better tables + OCR) via the Cloud Run docling service (ERP-047).

    Returns `{"text", "page_number"}` dicts, same shape as the fast path's normalized output.
    """
    return call_docling_service(pdf_path, settings)


def parse_pdf(
    pdf_path: str, settings: IngestionSettings
) -> tuple[list[dict[str, Any]], Literal["fast", "quality"]]:
    """Parse a PDF, using the fast path unless `needs_fallback` routes to the quality path."""
    fast_pages = parse_fast(pdf_path)

    if needs_fallback(fast_pages, settings.ocr_text_threshold):
        return parse_quality(pdf_path, settings), "quality"

    normalized = [
        {"text": page["text"], "page_number": page["metadata"]["page_number"]}
        for page in fast_pages
    ]
    return normalized, "fast"
```

- [ ] **Step 4: Remove `docling` from the root project's dependencies**

```toml
# pyproject.toml -- remove this line from `dependencies`:
    "docling>=2.117.0",
```

Run: `uv sync`
Expected: `uv.lock` updates, `docling`/`torch`/`transformers` (and their sub-dependencies) are
removed from the resolved lockfile and the `.venv`.

- [ ] **Step 5: Run the ingestion test suite to verify everything passes**

Run: `AUTH_JWT_SECRET_KEY=test uv run pytest tests/ingestion/ -v`
Expected: all pass, including the two updated fallback tests (now genuinely exercising the
mock instead of raising `AttributeError`).

- [ ] **Step 6: Run ruff and mypy on the changed files**

Run: `uv run ruff check app/ingestion/parsers.py tests/ingestion/test_parsers.py && uv run mypy app/ingestion/parsers.py`
Expected: both clean

- [ ] **Step 7: Commit**

```bash
git add app/ingestion/parsers.py tests/ingestion/test_parsers.py pyproject.toml uv.lock
git commit -m "feat: route docling parsing through Cloud Run, drop docling from root deps (ERP-047)"
```

---

### Task 5: Full backend verification

**Files:** `pyproject.toml` (one-line fix found during this step)

- [ ] **Step 0: Fix pytest's default collection scope**

Running the full suite for the first time after Task 4 surfaces a real problem: pytest's
default discovery scans the whole repo, including `deploy/cloud_run_docling/test_main.py` --
which now fails to import (`ModuleNotFoundError: No module named 'docling'`), exactly as
intended by Task 4 removing it from the root project, but pytest doesn't know that file is a
separate deployable unit's own test suite. Fix by scoping collection explicitly:

```toml
# pyproject.toml -- add to [tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 1: Run the full backend suite with coverage**

Run: `AUTH_JWT_SECRET_KEY=test uv run pytest -q --cov=app --cov-report=term-missing --cov-fail-under=90`
Expected: all pass, coverage gate holds. Note the suite should now be noticeably faster to
collect/run than before, since `docling`'s heavy import is gone from the whole test environment.

- [ ] **Step 2: Run ruff and mypy across the whole `app/` tree**

Run: `uv run ruff check . && uv run mypy app`
Expected: both clean

- [ ] **Step 3: Confirm `docling` is genuinely gone from the resolved environment**

Run: `uv run python -c "import docling"`
Expected: `ModuleNotFoundError` — confirms the dependency (and its `torch`/`transformers` weight)
is actually gone, not just removed from `pyproject.toml`'s text.

- [ ] **Step 4: Commit (only if any of the above required fixes; otherwise skip)**

```bash
git add -A
git commit -m "fix: address issues found in ERP-047 full-suite verification"
```

---

### Task 6: Deploy, document, and live-verify

**Files:**
- Modify: `docs/deployment.md` (new section)
- Modify: `.env.example` (new var)
- Modify: `.ai/tickets/ERP-047.md` (close out)
- Modify: `D:\github-projects\gcp-deployment-tracker.md` (new resource row — outside this repo's
  git history, per this project's established pattern for non-secret live-infra tracking)

This task is mostly live operational steps, not code. Run them in order.

- [ ] **Step 1: Enable the Cloud Run API on the project (if not already enabled)**

Run: `gcloud services enable run.googleapis.com --project self-hosted-rag-platform`

- [ ] **Step 2: Deploy the Cloud Run service**

Run (from the repo root):
```
gcloud run deploy self-hosted-rag-platform-docling \
  --source deploy/cloud_run_docling \
  --region us-central1 \
  --no-allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --timeout 600 \
  --concurrency 1 \
  --min-instances 0 \
  --max-instances 3 \
  --project self-hosted-rag-platform
```
Note the resulting service URL from the command's output.

- [ ] **Step 3: Find the VM's service account email**

Run: `gcloud compute instances describe rag-platform-host --zone us-central1-a --project self-hosted-rag-platform --format="value(serviceAccounts[0].email)"`

- [ ] **Step 4: Grant that service account permission to invoke the Cloud Run service**

Run:
```
gcloud run services add-iam-policy-binding self-hosted-rag-platform-docling \
  --region us-central1 \
  --member="serviceAccount:<EMAIL FROM STEP 3>" \
  --role="roles/run.invoker" \
  --project self-hosted-rag-platform
```

- [ ] **Step 5: Add the new env var to `.env.example`**

```
# .env.example -- append, with a comment explaining it
# Required once ERP-047 (docling offloaded to Cloud Run) is deployed. Without it, any PDF
# needing the quality fallback (tables/scanned pages) fails ingestion with a clear error
# instead of running docling in-process (which previously took the whole VM down under real
# load -- see docs/superpowers/specs/2026-09-17-docling-cloud-run-offload-design.md).
INGESTION_DOCLING_SERVICE_URL=https://self-hosted-rag-platform-docling-<hash>-uc.a.run.app
```

- [ ] **Step 6: Update the live VM's `.env` and redeploy the app**

SSH in (`gcloud compute ssh rag-platform-host --project self-hosted-rag-platform --zone us-central1-a`)
and run:
```
echo 'INGESTION_DOCLING_SERVICE_URL=<the real URL from Step 2>' >> ~/app/.env
cd ~/app && git pull && ~/.local/bin/uv sync && sudo systemctl restart rag-platform
```
`uv sync` here is what actually removes `docling`/`torch`/`transformers` from the VM's own
`.venv` — confirm afterward with `du -sh ~/app/.venv` before/after, or just proceed to the live
memory check in Step 8.

- [ ] **Step 7: Add a "docling parsing (Cloud Run)" section to `docs/deployment.md`**

```markdown
## Docling parsing (Cloud Run)

ERP-047: `docling`'s quality-fallback parser (tables/scanned pages) runs on a separate Cloud
Run service, not in-process on the VM -- its own documented ~4GB baseline memory requirement
took the whole VM down once under real load. `INGESTION_DOCLING_SERVICE_URL` must be set for
this to work; see `deploy/cloud_run_docling/README.md` for the deploy command and
`app/ingestion/cloud_run_client.py` for how the VM authenticates to it (GCP IAM identity token
from the metadata server, no stored secret).
```

- [ ] **Step 8: Live-verify with a real PDF that triggers the fallback path**

Upload a PDF with a table or scanned content through the live deployed frontend (or `curl`
directly against `/ingestion/pdf`), poll its job status until `DONE`, and confirm the result
looks correct. While that's processing, check the VM's own memory via SSH (`free -h`) —
confirm it stays roughly flat (no longer spiking), proving the heavy work genuinely left the
VM's process.

- [ ] **Step 9: Update `D:\github-projects\gcp-deployment-tracker.md`**

Add a row for the new Cloud Run service (URL, region, memory/CPU/concurrency settings, the IAM
binding) to the "What's provisioned" table, following the existing row format.

- [ ] **Step 10: Close out `.ai/tickets/ERP-047.md`**

Mark the remaining acceptance criteria `[x]`, add a `## Resolution` section summarizing what was
built and the live-verification result (the memory-stays-flat confirmation from Step 8), set
`Status: Done`.

- [ ] **Step 11: Update `.ai/memory/current-state.md`**

Move ERP-047 out of the "Open tickets" list (or mark it done inline) in the Next Planned Work
section, and add a "What Exists" bullet summarizing the fix, following this file's existing
style for closed-out tickets.

- [ ] **Step 12: Commit the docs/tracker changes**

```bash
git add docs/deployment.md .env.example .ai/tickets/ERP-047.md .ai/memory/current-state.md
git commit -m "docs: close out ERP-047 with live docling-on-Cloud-Run verification"
```

(The `gcp-deployment-tracker.md` edit from Step 9 lives outside this repo's git history —
nothing to commit here for it, per this project's established pattern.)
