# Session — Docling Cloud Run Offload, Live Deployment, and Modal Billing Fix

Date: 2026-09-18
Tickets Touched: ERP-047

## Decisions

- Built ERP-047 per the design/plan written the previous session (2026-09-17): offload
  `docling`'s PDF-parsing fallback to a separate Cloud Run service, keeping full extraction
  quality rather than trading down to lighter libraries.
- Live deployment surfaced two real infrastructure bugs beyond what the design anticipated
  (see Implementation Summary) — both fixed and re-verified before considering the ticket done,
  not just fixed-in-theory.
- Confirmed (via research, not assumption) that Modal's advertised "$30/mo free" only applies
  once a payment method is on file; without one, usage caps at $1 and the workspace disables.
  User added a card and set a **$0 spend limit** — Modal's hard-stop-on-any-real-charge setting
  — so the account stays genuinely free by design rather than relying on remembering to check.

## Implementation Summary

Followed `docs/superpowers/plans/2026-09-17-docling-cloud-run-offload.md` task-by-task (inline
execution, this session). All 6 tasks completed:

1. `IngestionSettings` gained `docling_service_url`/`docling_service_timeout_seconds`.
2. `app/ingestion/cloud_run_client.py` — new HTTP client, GCP IAM identity-token auth via the
   metadata server (no stored secret), tested against `httpx.MockTransport` mirroring the
   existing OIDC test pattern.
3. `deploy/cloud_run_docling/` — new standalone FastAPI service wrapping `docling`. Smoke-tested
   locally with a real PDF (while `docling` was still available in the main venv, pre-removal) —
   this caught a real Windows temp-file bug (`NamedTemporaryFile`'s default exclusive-open mode)
   before it ever reached deployment.
4. `app/ingestion/parsers.py`'s `parse_quality()` rewired to call the new client;
   `docling`/`torch`/`transformers` removed entirely from the root project (72 packages gone
   from `uv.lock`, including CUDA/nvidia wheels the VM never needed).
5. Full backend verification: 394 passed (was 387), 96.62% coverage. Found and fixed one more
   issue here: pytest's default collection scope picked up the new service's own
   `test_main.py`, which correctly can't import `docling` any more — fixed with
   `testpaths = ["tests"]`.
6. Live deployment (PR #40, merged): Cloud Run service deployed
   (`self-hosted-rag-platform-docling`, `min-instances=0`, `concurrency=1`, `max-instances=3`,
   IAM-only access granted to the VM's service account), VM's `.env` updated, `uv sync` removed
   `docling` from the live VM, service restarted.

**Two more real bugs found during live deployment itself, fixed in a follow-up PR (#41)**:
- The first live upload attempt failed: the container was fetching `docling`'s model weights
  from HuggingFace at *request* time (not baked in), hit HF's rate limit, and the job failed
  outright. Fixed by running `docling-tools models download` at Docker *build* time and pointing
  `DocumentConverter` at the local baked-in path explicitly via `PdfPipelineOptions
  (artifacts_path=...)` — mirrors `deploy/modal_ollama.py`'s existing "bake models at build
  time" pattern, verified locally (zero network calls) before redeploying.
- The rebuild then failed at the Docker build step itself: `rapidocr` (one of `docling`'s OCR
  backends) needs `opencv-python`, which needs X11/GL system shared libraries
  (`libxcb.so.1` etc.) that `python:3.12-slim` doesn't ship — a well-known "opencv in a slim
  Docker image" issue, fixed with a short `apt-get install`.

**Live-verified end-to-end** after all fixes: `POST /parse` returned `200 OK` with zero
HuggingFace calls (Cloud Run logs confirmed models loading from the baked-in cache); the live
VM's memory held at 266-321Mi available throughout upload processing — matching the
pre-incident baseline exactly, no spike (the whole point of the fix).

**Separate issue found during this same verification, fixed same session**: the pipeline's next
stage (embedding, via Modal) started failing with `"workspace is disabled"`. Root-caused to
Modal's free-tier structure (usage at $1.03, over the $1-without-a-card threshold). User added a
payment method and set a $0 spend limit. Re-verified live: `POST /generation/query` returned
`200` with a correct, grounded, cited answer.

## Blockers

None remaining. ERP-047 is `Done`. The live deployment is fully functional end-to-end
(ingestion including the `docling` fallback, retrieval, generation) as of this session.

## Next Steps

- One remaining loose end from ERP-043 (not this session's ticket, but adjacent): a final
  walkthrough of the actual deployed frontend UI, now that both the `docling` fallback and
  Modal/chat are confirmed working via direct API verification. Not yet clicked through the real
  UI since both fixes landed.
- ERP-044 (document-scoped retrieval), ERP-045 (answer feedback), ERP-046 (retrieval relevance
  guardrail) remain open backlog items, un-started, each needing its own design pass.
