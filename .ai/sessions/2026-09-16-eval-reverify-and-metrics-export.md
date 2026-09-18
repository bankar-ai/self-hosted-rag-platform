# Session — Retrieval-Quality Re-verification and Metrics Export to Grafana Cloud

Date: 2026-09-16
Tickets Touched: ERP-041, ERP-042

## Decisions

- Turned two items from `current-state.md`'s "Next Planned Work" into real tickets (ERP-041,
  ERP-042) rather than doing the work untracked.
- ERP-042 reused ERP-038/ERP-039's established precedent (push-based in-process OTLP export,
  measured to cost no detectable memory) rather than reopening the agent-vs-push-based debate —
  the live VM's RAM constraint hasn't changed, so the same conclusion applies.

## Implementation Summary

**ERP-041** (pure verification, no code change): ran `uv run python -m app.evaluation.run`
against real local Ollama + Postgres/Redis (after applying all 11 Alembic migrations to the local
Postgres volume, which hadn't been run against it yet). Result: Precision@3=0.333, Recall@3=1.000,
MRR=1.000 — an exact match to the ERP-029 baseline. Confirmed via direct SQL that no eval user or
document rows were left behind. No PR — nothing to merge.

**ERP-042**: `app/core/telemetry.py` gained `_build_otlp_metric_exporter()` (mirrors
`_build_span_exporter()`'s protocol selection) and `_build_metric_readers()`, which returns both
the existing `PrometheusMetricReader()` and a new `PeriodicExportingMetricReader` over OTLP.
`MeterProvider` now uses both readers simultaneously — local dev keeps working via `/metrics`
unchanged, and the live deployment's metrics now also push to Grafana Cloud. No new dependency, no
new env vars, no new Grafana Cloud token (the existing `set:alloy-data-write` token already covers
metrics). Added two tests mirroring `test_logging_config.py`'s protocol-selection pattern.

Deployed via the standard flow: feature branch → PR #34 (`develop`) → PR #35 (`develop` → `main`)
→ `git pull` + `uv sync` + `systemctl restart` on the live VM. Both PR merges were blocked for
Claude by the auto-mode permission classifier (same as every prior PR in this project) — the user
merged both directly, and ran the SSH deploy command themselves after a `gcloud` vs `gcloud.cmd`
PowerShell hiccup (same known gotcha already documented in `gcp-deployment-tracker.md`).

Live verification: the user checked the Grafana Cloud Explore UI directly (no read-scoped API
token exists yet, only the write-scoped ingestion token — discussed with the user and deliberately
not set up this session, see Decisions) and confirmed `http_server_duration_milliseconds_bucket`,
`http_server_active_requests`, and `db_client_connections_usage` series filtered by
`service_name="self-hosted-rag-platform"`, populated from the real `/docs` and `/auth/login`
requests made during this session. Hand-written metrics like `llm_generation_duration_seconds`
weren't separately exercised (would need an authenticated `/generation/query` call), but they flow
through the identical reader chain, so there's no separate code path left unverified.

VM memory re-checked post-deploy: 368Mi available, matching the existing 365-378MB baseline exactly
— confirms the added export path is free, as expected.

## Blockers

None remaining. Both tickets closed.

## Next Steps

- A read-scoped Grafana Cloud API token was discussed but deliberately not created this session —
  worth revisiting once a future project (Agentic AI, PEFT, LLMOps) starts reusing this same
  Grafana Cloud stack, at which point self-service verification becomes worth the extra credential
  to manage.
- Remaining items in `current-state.md`'s "Next Planned Work" are all pre-existing deferred items
  (OIDC self-service linking, non-Google OIDC providers, admin cross-user data visibility,
  self-service admin creation, the pre-ERP-031 FAISS migration note) — none surfaced or changed by
  this session.
