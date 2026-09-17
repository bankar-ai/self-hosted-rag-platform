# Offload `docling` Parsing to Cloud Run — Design Spec

Status: Approved (brainstorming)
Date: 2026-09-17
Related ticket: `.ai/tickets/ERP-047.md`
Related principle: `docs/architecture.md`'s "keep the always-on host thin" (added same day)

## Purpose

A live PDF upload took the production VM fully unresponsive (not just the app — SSH too) for
~40 minutes, requiring a manual `gcloud compute instances reset`. Root cause: `app/ingestion/
parsers.py`'s `parse_quality()` runs `docling` in-process, and `docling`'s own documented
baseline (~4GB RAM) vastly exceeds this VM's 958MB. This is not about file size — any PDF with a
table or scanned/image content trips `needs_fallback()` regardless of how big the file is.

Chosen fix (over swapping `docling` for lighter libraries, or offloading to Modal): move
`docling` itself to a separate Google Cloud Run service in the same GCP project. This keeps
`docling`'s full extraction quality (no permanent tradeoff), costs effectively nothing at this
project's scale (Cloud Run's free tier is a recurring monthly allowance, not a spend-down
credit — sized against realistic usage in the ticket), and needs no GPU (Modal is GPU-priced,
the wrong fit for a CPU-only job). Immediate mitigation (2GB swap on the VM) is already done
separately — this spec covers the actual fix.

## Scope

In scope:
- A new Cloud Run service running `docling`'s `DocumentConverter`, called over HTTP
- `app/ingestion/parsers.py`'s `parse_quality()` calls that service instead of importing
  `docling` in-process
- IAM-based auth (VM's service account → Cloud Run `run.invoker`), no stored secrets
- `docling`/`torch`/`transformers` removed from the VM's own installed dependencies entirely
- Timeout + `FAILED`-job handling for a slow/failed call, reusing existing job-failure plumbing

Out of scope (deferred, not part of this fix):
- Changing `needs_fallback()`'s routing logic (still fast-path-first, same trigger conditions)
- Any frontend change — `DocumentsPage`'s existing pending/processing polling UI already handles
  an arbitrarily long wait with no changes needed
- Always-warm Cloud Run (`min_instances=1`) — deliberately not used, see Architecture

## Architecture

A new `deploy/cloud_run_docling/` directory: a minimal FastAPI app (`main.py`) with one route,
`POST /parse`, wrapping today's `parse_quality()` body almost verbatim — receives raw PDF bytes,
runs `docling.document_converter.DocumentConverter`, returns the same `[{"text": str,
"page_number": int}, ...]` shape the caller already expects. Its own `Dockerfile` installs
`docling` (and its `torch`/`transformers` transitive deps) — isolated to this one image, never
installed on the VM.

`min_instances=0` (scales to zero between uploads, stays within the free tier) — cold-start
latency after idle is tolerable since ingestion is already an async background job with existing
poll-based status UI; nothing needs to wait synchronously on this.

Auth is GCP-native IAM: the VM's own service account fetches a short-lived OIDC identity token
directly from the GCE metadata server (a plain HTTP GET, no new dependency — `httpx` already
exists in this project for the OIDC integration) and sends it as a Bearer token. Cloud Run is
configured to require authentication and grants `run.invoker` only to the VM's service account.
No shared secret exists anywhere.

## Components

- **`deploy/cloud_run_docling/main.py`** — the Cloud Run service itself. One route:
  `POST /parse` (multipart file upload) → `list[{"text": str, "page_number": int}]`. Contains
  today's `parse_quality()` logic moved here verbatim (the `DocumentConverter` call, the
  `_PAGE_BREAK`-based page splitting).
- **`deploy/cloud_run_docling/Dockerfile`** — a standard Python image with `docling` installed;
  built and deployed independently of the VM's own deploy flow (mirrors `deploy/modal_ollama.py`
  being a separate deployable unit from the main app).
- **`app/ingestion/cloud_run_client.py`** (new) — `fetch_identity_token(audience: str) -> str`
  (metadata-server call) and `call_docling_service(pdf_path: str, settings: IngestionSettings)
  -> list[dict[str, Any]]` (reads the file, gets the token, POSTs to
  `settings.docling_service_url`, returns the parsed result or raises `DoclingServiceError` on a
  non-200 response or timeout).
- **`app/ingestion/config.py`**'s `IngestionSettings` gains `docling_service_url: str` (no
  default — required once this ships, same "must be set explicitly" pattern as
  `AUTH_JWT_SECRET_KEY`) and `docling_service_timeout_seconds: float = 480.0` (8 minutes).
- **`app/ingestion/parsers.py`**'s `parse_quality()` — becomes a thin wrapper delegating to
  `cloud_run_client.call_docling_service()`. The function signature and return shape are
  unchanged, so `parse_pdf()` and everything above it needs no changes at all.

## Data Flow

1. `parse_pdf()` calls `parse_fast()`, then `needs_fallback()` — unchanged.
2. If fallback is needed, `parse_quality(pdf_path)` calls
   `cloud_run_client.call_docling_service(pdf_path, settings)`.
3. `call_docling_service` reads the PDF bytes, calls `fetch_identity_token(settings.
   docling_service_url)` (a GET to `http://metadata.google.internal/computeMetadata/v1/
   instance/service-accounts/default/identity?audience=<url>` with header
   `Metadata-Flavor: Google`), then `POST`s the PDF bytes + `Authorization: Bearer <token>` to
   `{docling_service_url}/parse`, with an 8-minute timeout.
4. Cloud Run's `main.py` receives the bytes, runs `DocumentConverter`, returns the parsed page
   list as JSON.
5. `call_docling_service` returns that list; `parse_quality()` returns it unchanged;
   `run_ingestion_job()` proceeds exactly as it does today.

## Error Handling

A timeout, non-200 response, or network failure from `call_docling_service` raises
`DoclingServiceError` (a new exception in `cloud_run_client.py`), which propagates up through
`run_ingestion_job()`'s existing try/except — the job is marked `FAILED` with the error message,
identical to how any other parsing failure is already handled today. No new failure-handling
code path is needed anywhere above `parse_quality()`.

## Testing

- `deploy/cloud_run_docling/`: a minimal test using FastAPI's `TestClient` against `main.py`
  directly (not deployed for real in CI, same "never hit real external services in CI"
  philosophy already used for Ollama/Modal).
- `app/ingestion/cloud_run_client.py`: unit tests mocking the HTTP calls (success, non-200,
  timeout) — no real metadata server or Cloud Run call in CI.
- `app/ingestion/parsers.py`'s existing `test_parse_pdf_falls_back_to_quality_for_table`/
  `test_parse_pdf_falls_back_to_quality_for_scanned_page` (currently exercise real `docling`)
  get updated to mock `call_docling_service` instead — `docling` is no longer importable in the
  main project's test environment at all once it's removed from `pyproject.toml`.

## Dependency Changes

- **Removed** from the root `pyproject.toml`: `docling` (and its transitive `torch`/
  `transformers` pull, which was the actual memory cost). This also meaningfully shrinks local
  dev/CI install time and size — a real side benefit, not just the production fix.
- **No new dependency** for the identity-token fetch — reuses `httpx`, already present.
- `deploy/cloud_run_docling/` gets its own `pyproject.toml`/`requirements.txt` with `docling` +
  `fastapi`/`uvicorn`, isolated from the main project's dependency tree entirely.

## Deployment

Manual steps (documented in `docs/deployment.md`, mirroring how `deploy/modal_ollama.py`'s
deployment is documented): build and deploy the Cloud Run service (`gcloud run deploy`), grant
the VM's service account `run.invoker` on it, set `INGESTION_DOCLING_SERVICE_URL` in the VM's
`.env`, `uv sync` (drops `docling` from the VM's venv), restart `rag-platform.service`. Live
verification: upload a real PDF that triggers the fallback path, confirm it completes
successfully, and confirm the VM's own memory stays flat during that upload (the whole point).
