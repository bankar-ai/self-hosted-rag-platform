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

## Cross-project infrastructure options

Hosting/compute/database/GPU choices for making this platform (and future sibling projects)
reachable for a bounded test window are tracked outside this repo, at
`D:\github-projects\infrastructure-options.md` (see ADR-008) -- not duplicated here.
