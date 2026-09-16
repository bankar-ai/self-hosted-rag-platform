# Session — Grafana Cloud Traces (ERP-038)

Date: 2026-09-15
Tickets Touched: ERP-038

## Decisions

- Grafana Cloud's OTLP gateway for this stack (`https://otlp-gateway-prod-ap-south-1.grafana.net/otlp`)
  is a path-based HTTPS URL, confirming it expects **OTLP/HTTP**, not the gRPC transport
  `app/core/telemetry.py` had hardcoded — resolved by making the exporter protocol-aware via the
  standard `OTEL_EXPORTER_OTLP_PROTOCOL` env var, rather than assuming gRPC would work everywhere.
- Narrowed the Grafana Cloud API token's intent to traces-only (matching ERP-038's explicit scope),
  though the quick-setup UI only offers a bundled `set:alloy-data-write` scope (also covers
  metrics/logs writes) — accepted as a non-issue since this is a single-user account, not a
  multi-tenant credential, and the app only ever sends traces.
- Added `OTEL_SERVICE_NAME=self-hosted-rag-platform` after live verification showed traces landing
  as `unknown_service` — not in the original plan, but a same-session fix once observed.

## Implementation Summary

- `app/core/telemetry.py`: new `_build_span_exporter()` picks the OTLP exporter class
  (`grpc` vs `http/protobuf`) based on `OTEL_EXPORTER_OTLP_PROTOCOL`. No new dependency — both
  exporter classes ship in the `opentelemetry-exporter-otlp` meta-package already present.
- `.env.example`: documented the three new optional OTel env vars, including the Python-specific
  `Basic%20` header-encoding quirk (a literal space in the header value must be percent-encoded,
  since the OTel SDK's env-var header parser splits on raw spaces).
- `docs/deployment.md`: new "Traces (Grafana Cloud)" section — where traces go, how to look at them.
- Committed to `develop` (commit `42d8aab`) and pushed; deployed to the live VM
  (`rag-platform-host`): `git pull` + `uv sync`, appended
  `OTEL_EXPORTER_OTLP_ENDPOINT`/`_PROTOCOL`/`_HEADERS`/`OTEL_SERVICE_NAME` to `~/app/.env`,
  `systemctl restart rag-platform`.
- `D:\github-projects\gcp-deployment-tracker.md`: added a "Traces (Grafana Cloud)" section with
  non-secret operational details (stack name, region, how to check).
- `C:\Users\Pankaj\.credentials\self-hosted-rag-platform-credentials.md`: added the Grafana Cloud
  instance ID and API token.
- Verified end-to-end with real traffic: registered a throwaway test user
  (`erp038-verify@example.com`, left in the live DB — no delete-user endpoint exists yet, harmless),
  logged in, ran a real `POST /retrieval/query`, and confirmed in Grafana Cloud's Explore -> Tempo
  view a trace rooted at `self-hosted-rag-platform POST /retrieval/query` with
  `embedding.generate`/`faiss.search`/`bm25.search`/`retrieval.fuse` correctly nested underneath.
  Logged the test user's session out afterward.
- Verified locally: `ruff`/`mypy` clean; full suite 369 passed against real Postgres/Redis/Jaeger
  (started via `docker compose up -d postgres redis jaeger`, which weren't already running).

## Blockers

None — ERP-038 is fully resolved.

## Next Steps

- ERP-039 (Backlog): ship the live deployment's application logs to Grafana Cloud — the
  deliberately-deferred second half of this ticket's original scope.
- Metrics export to Grafana Cloud is not yet ticketed (current Prometheus exporter is pull-based;
  the VM has nothing to scrape it, and there's no established acceptable approach yet — remote-write
  agent vs push-based OTLP metrics).
- Consider a delete-user path (admin or self-service) at some point — this session left one
  harmless throwaway test user row in the live Neon database with no way to remove it via the API.
