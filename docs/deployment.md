# Deployment Notes

Operational guidance discovered through live deployment-readiness testing (2026-09-08) that
doesn't belong in `docs/architecture.md`'s design-level content. See ERP-034, ERP-035, ERP-037.

## Model tags must match exactly what's installed

Ollama resolves an untagged model name (e.g. `"qwen3"`) to an implicit `:latest` tag only -- it
does **not** fall back to whatever tag you actually pulled (e.g. `"qwen3:8b"`). If
`GENERATION_MODEL`/`EMBEDDING_MODEL` don't match an installed tag exactly, generation/embedding
calls fail on the first real request, not at startup.

Before deploying (or after installing/changing a model on the target host), run:

```
uv run python -m app.core.check_models
```

This checks both `GenerationSettings.model` and `EmbeddingSettings.model` against `ollama list` on
their configured hosts and exits non-zero if either is missing. Wire this into any deployment
script as a pre-flight check (see ERP-037) rather than discovering a mismatch from a live 500.

## Generation provider: self-hosted Ollama vs. hosted OpenRouter (ERP-106)

`GenerationSettings.provider` (`GENERATION_PROVIDER`) selects which `LLMClient` implementation
`app/generation/client.py`'s `get_default_llm_client` builds:

- `"ollama"` (default, unchanged): self-hosted/Modal-backed, via `GENERATION_OLLAMA_HOST`. Free
  at this project's usage scale, but pays a real scale-to-zero cold-start cost when idle
  (measured at 75-250+ seconds in live traces -- see ERP-097's session log) -- both the
  `POST /generation/warmup` mitigation (ERP-091) and this cost are specific to this provider.
- `"openrouter"`: routes to OpenRouter's always-on, shared hosted infrastructure via its
  OpenAI-compatible API (`GENERATION_OPENROUTER_API_KEY`/`GENERATION_OPENROUTER_MODEL`/
  `GENERATION_OPENROUTER_BASE_URL`). No cold start to hide -- `warmup_llm`'s ping is a
  documented no-op for this provider -- but a real per-token cost (cheap for an open model like
  Gemma; check OpenRouter's current pricing page before choosing a model).

Switching is a config-only change -- no code, no migration. `EMBEDDING_MODEL`/embedding
generation is **not** affected by this setting; embeddings remain on the self-hosted/Modal path
regardless, since moving them would mean re-embedding the entire FAISS index against a different
model (a separate, larger decision, not yet made).

## Hardware / VRAM sizing

There is no universal safe number -- it depends on the exact model tag, its quantization, and the
context length in use. What's been verified live on a 6GB-VRAM / 16GB-system-RAM laptop:

- **`nomic-embed-text`** (137M params, ~274MB on disk): negligible footprint, runs comfortably
  alongside anything else.
- **`gemma3:4b`** (~3.3GB on disk, Q4_K_M): loads and runs reliably even under moderate concurrent
  system load. **Recommended default for constrained or shared hardware** -- verified live to give
  faithful, correctly-cited, non-hallucinated answers (see the 2026-09-08 session log).
- **`qwen3:8b`** (~5.2GB on disk, Q4_K_M): works on a 6GB-VRAM GPU, but only with real headroom
  free. Verified live to intermittently fail with out-of-memory errors -- both a CUDA VRAM
  allocation failure and a CPU-pinned-buffer allocation failure were observed on the same machine,
  at different times, purely as a function of how much RAM other running processes (IDE, browser,
  WSL/Docker backend, etc.) were using at that moment. The same model loaded and ran correctly once
  system RAM pressure eased -- this is **not deterministic given "enough total RAM" on paper**; it
  depends on actual concurrent load at call time.

**Practical implication**: don't assume a model that fits a GPU's VRAM on a spec sheet will reliably
load on a machine that's also running other things. If deploying on genuinely dedicated hardware
(nothing else running), sizing is closer to the spec-sheet numbers; if sharing hardware with a dev
environment or other workloads, prefer a smaller model with real headroom (`gemma3:4b` over
`qwen3:8b` on a 6GB card) or move generation to dedicated/serverless GPU hosting (see
`D:\github-projects\infrastructure-options.md`) rather than fighting for local resources.

## Traces (Grafana Cloud)

ERP-028 instruments the whole RAG pipeline with OpenTelemetry, but the live deployment (ERP-037)
has nowhere to run a local trace backend the way `docker-compose.yml`'s Jaeger service does for dev
-- the `e2-micro` VM has no RAM headroom for another local service. ERP-038 instead points the app
at Grafana Cloud's free tier (50GB traces/month, hosted, no self-hosting).

`app/core/telemetry.py`'s `_build_span_exporter()` picks the OTLP exporter based on the standard
`OTEL_EXPORTER_OTLP_PROTOCOL` env var -- `"grpc"` (default, unset) matches local Jaeger on port
4317 unchanged; `"http/protobuf"` is what Grafana Cloud's OTLP gateway requires. See
`.env.example` for the exact three env vars (`OTEL_EXPORTER_OTLP_ENDPOINT`/`_PROTOCOL`/`_HEADERS`)
and the Python-specific `Basic%20` header-encoding quirk.

**To look at a live trace**: log into Grafana Cloud, open stack `microstarfish1843`, go to
**Explore**, select the **Tempo** datasource, and search by service name or trace ID. Each request
that hits the RAG pipeline produces a trace with the hand-written pipeline spans
(`embedding.generate`, `faiss.search`, `bm25.search`, `retrieval.fuse`/`rerank`/`expand_sections`,
`llm.generate`) nested under the FastAPI request span -- this is the fastest way to see which stage
was slow or failed for a real production request, without SSHing in to read raw journald output.

## Application logs (Grafana Cloud)

ERP-039 extends the same Grafana Cloud stack to application logs, in-process -- no separate
log-shipping agent (Alloy/Promtail), which would be another always-running process competing for
the VM's tight RAM budget. `configure_logging()` (`app/core/logging_config.py`) attaches an OTLP
`LoggingHandler` alongside the existing stdout handler, reusing the same `OTEL_EXPORTER_OTLP_*` env
vars and protocol-selection logic as the trace exporter above. Measured live: this added no
detectable memory overhead (uvicorn RSS and system-wide available memory were unchanged before vs
after deploying it).

**To look at live logs**: same Grafana Cloud stack (`microstarfish1843`) -> Explore -> the
**Loki** datasource (`grafanacloud-microstarfish1843-logs`) -> filter by
`service_name="self-hosted-rag-platform"`. Each log line carries `trace_id`/`span_id` fields and a
"Links -> traceID" affordance that pivots straight to the matching trace in Tempo -- the same
log-to-trace correlation `TraceIdFilter` was built for, now working against the live backend.

## Metrics (Grafana Cloud)

ERP-042 extends the same stack to metrics. The existing pull-based `PrometheusMetricReader`
(backing the local `/metrics` endpoint `docker-compose`'s Prometheus service scrapes) is kept for
local dev, and a push-based `PeriodicExportingMetricReader` over OTLP is added alongside it --
same protocol-selection pattern (`_build_otlp_metric_exporter()` in `app/core/telemetry.py`), same
`OTEL_EXPORTER_OTLP_*` env vars, no new Grafana Cloud token. This closes the gap traces (ERP-038)
and logs (ERP-039) left open: hand-written metrics (`embedding_cache_requests_total`,
`retrieval_cache_requests_total`, `llm_generation_duration_seconds`, `ingestion_jobs_total`) and
auto-instrumented ones now reach Grafana Cloud without needing a local Prometheus to scrape the VM.

**To look at live metrics**: same Grafana Cloud stack (`microstarfish1843`) -> Explore -> the
**Prometheus/Mimir** datasource, query by metric name (e.g. `llm_generation_duration_seconds`) or
filter by `service_name="self-hosted-rag-platform"`.

## LLM tracing (Langfuse Cloud, ERP-112)

The traces/logs/metrics above (ERP-028/038/039/042) cover the whole app but have no LLM-specific
detail -- no browsable per-call prompt/response, no token usage. Langfuse Cloud (free Hobby tier)
fills that gap, complementary to (not a replacement for) the existing OTel+Grafana Cloud stack.

`app/generation/tracing.py`'s `trace_generation()` wraps every `OllamaLLMClient`/
`OpenRouterLLMClient` `generate`/`generate_stream` call, nested inside the existing `llm.generate`
OTel span rather than replacing it -- Langfuse's SDK (v4, OTel-native) attaches its own span
processor to the same global `TracerProvider` `app/core/telemetry.py` already configures, so a
Langfuse "generation" observation shows up as a child of that span, not a second disconnected
trace. Captures the system+user prompt, model, response text, and token usage where the provider
surfaces it (Ollama's `prompt_eval_count`/`eval_count`; OpenRouter's `usage` block) -- non-streaming
calls only; streaming responses are traced for prompt/output but not token usage, a known gap.

Purely additive and never load-bearing, same philosophy as this repo's Redis caches (ADR-003): if
`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` aren't set, or the SDK fails to initialize, every call
becomes a no-op -- nothing else in the request path changes or fails.

**To look at live traces**: [cloud.langfuse.com](https://cloud.langfuse.com) -> the
`self-hosted-rag-platform` project -> **Tracing**. Each trace shows the full prompt/response pair,
model, and token counts (when captured) for one `/generation/query` call.

**Scope note**: covers live `/generation/query` traffic only. The offline `app/evaluation/`
retrieval/generation-quality runs (ERP-029/030) are not wrapped -- left as a possible follow-up.

### Automated scoring (Langfuse Evaluators, added 2026-09-28)

Every trace above also gets an automated grounding/hallucination score, configured entirely in
the Langfuse UI -- no code in this repo. Recorded here since it's config that lives only in
Langfuse's project settings, not in git, and would otherwise be invisible to anyone reading this
repo.

**LLM Connection** (Project Settings -> LLM Connections): provider name `modal-ollama`, adapter
`openai` (Ollama's OpenAI-compatible endpoint), base URL
`https://pankajkumar-bankar--self-hosted-rag-platform-ollama-olla-6f6aea.modal.run/v1`, custom
model `gemma3:4b`. Deliberately **not** OpenRouter or a hosted provider -- this reuses the same
free, already-deployed Modal Ollama endpoint `GENERATION_OLLAMA_HOST`/`EMBEDDING_OLLAMA_HOST`
already point at (ERP-037), so judge calls cost nothing beyond existing Modal compute. Important
gotcha: Langfuse Cloud's Evaluators run on Langfuse's own servers, not locally -- a `localhost`
Ollama endpoint is unreachable from there; the endpoint must be public, which Modal's already is.

**Evaluator** ("Evaluators" -> New Evaluator): name `response-quality`, type LLM-as-a-judge,
using the `modal-ollama`/`gemma3:4b` connection above. Prompt:

```
Evaluate whether the response is factually grounded in the input and free of hallucination or
fabricated claims. Return 1 if fully grounded, 0 if it contains unsupported claims.

Input: {{input}}
Response: {{output}}
```

Score: a number between 0 and 1. Variables auto-mapped (`{{input}}` -> observation input,
`{{output}}` -> observation output). Rule: reuses the evaluator's own sample filter
(`isRootObservation:true`, i.e. root generation observations only), **100% sampling** (not
throttled -- current traffic is low enough, per `current-state.md`'s 5-20 sporadic test users,
that full coverage costs nothing meaningful against the Hobby tier's 50k units/month; revisit if
volume grows), past observations backfilled once at setup time.

Cold-start note: since the judge calls the same scale-to-zero Modal endpoint generation uses, a
score can take 50-100s to appear if the container had gone idle. This is a non-issue in practice
-- scoring is asynchronous background work with nobody waiting on it, unlike a live user request
(which is why `ERP-091`'s `ping()` warmup trick exists for generation but nothing equivalent was
needed here).

**To look at scores**: same Langfuse project -> **Tracing**, `Scores` column on each trace row; or
**Scores** in the left nav for a dedicated list/filter view.

## Frontend (Vercel)

ERP-043 adds a Vite + React SPA (`frontend/`) deployed separately to Vercel, calling this
backend directly over HTTPS. See `frontend/README.md` for local dev and deployment steps.

The backend needs `CORS_ALLOWED_ORIGINS` (comma-separated, `app/core/cors.py`) set to the
deployed frontend's origin -- without it, the browser blocks every cross-origin request
outright. Local dev defaults to `http://localhost:5173` (Vite's default port) with no env var
needed.

## Upload size ceiling (ERP-051)

`INGESTION_MAX_UPLOAD_SIZE_BYTES` is set to 20MB (`app/ingestion/config.py`) -- previously 50MB,
an arbitrary resource-exhaustion guard never validated against real capacity. Live-verified
against the actual deployment (2026-09-18), not assumed:

- **A realistic ~15MB PDF** (6 pages, image-heavy -- the shape most real "large" PDFs actually
  are) processed via the fast path in **~75-90 seconds** end to end, VM memory stable throughout.
- **A deliberately pathological 18MB PDF** (1305 pages of dense text -- an unusually high
  page/chunk count for that byte size, not representative of typical uploads) took **30+ minutes**
  and was still processing when last checked. VM memory stayed bounded (never below ~250MB
  available) and the service never became unresponsive, but this is not an acceptable user-facing
  wait. The 20MB byte-size cap alone does not bound processing time for pathological
  page-dense content -- a future page-count or chunk-count limit may be worth adding if this
  shape of upload turns out to be common in practice (not observed yet, this was a synthetic
  worst-case test).
- **Scanned/OCR-fallback path** (ERP-059, below) separately verified at a smaller size.

**Practical takeaway**: 20MB is a safe *byte-size* ceiling for realistically-shaped PDFs. It does
not, by itself, bound worst-case processing time for an unusually page-dense document at that
same size.

## Scanned/image-based PDFs (OCR) — live-verified (ERP-059)

Confirmed end-to-end against the live deployment (2026-09-18): a real image-based PDF (rendered
text with no text layer, forcing the OCR fallback) was correctly routed to the Cloud Run
`docling` service (`parser_used: "quality"` in the job result), and the OCR-extracted text was
byte-for-byte correct against the source image. Took ~2 minutes end to end (Cloud Run cold start
included, well within its 480s/600s client/service timeout budget). VM memory was completely
flat throughout (~388-397MB used, matching baseline) -- confirming the OCR workload genuinely
never touches the VM's own tight memory budget, as ERP-047 designed it to.

## Concurrent-upload capacity (ERP-052)

Live-verified (2026-09-18): **5 concurrent PDF uploads from 5 different users completed
successfully within ~20 seconds**, including while an unrelated pathological large-upload job
(above) was also still running in the background -- i.e. this result holds even under
additional load, not just in isolation. VM memory recovered to baseline immediately after.

Higher concurrency (10+) was not tested in this pass, to avoid compounding risk on a
single-instance `e2-micro` that has previously gone fully unresponsive under load (ERP-047).
**5 concurrent uploads is the tested, promised number** as of this writing; a follow-up ticket
should push further under cleaner conditions (without a competing pathological job in flight) if
a higher number is needed.

## Cross-project infrastructure options

Hosting/compute/database/GPU choices for making this platform (and future sibling projects)
reachable for a bounded test window are tracked outside this repo, at
`D:\github-projects\infrastructure-options.md` (see ADR-008) -- not duplicated here.
