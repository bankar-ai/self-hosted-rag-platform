# Session — ERP-087/089/090 Grafana dashboards, VM redeploy

Date: 2026-09-25
Tickets Touched: ERP-087, ERP-089, ERP-090 (all Done)

## Decisions

- Built all three Grafana dashboard tickets as dashboard-as-code (`deploy/grafana/dashboards/`),
  extending the existing "AI Platforms — Service Observability" dashboard rather than creating
  separate ones, matching the tickets' own "companion" framing.
- ERP-089's per-pipeline-stage latency needed Tempo TraceQL metrics, not PromQL — the retrieval
  sub-stage spans (ERP-028) were never backed by a Prometheus histogram. Confirmed TraceQL
  metrics work on this Grafana Cloud plan by testing directly against the API before committing
  to the approach (capped at a 25h query window, a real Tempo-side limit).
- ERP-090's Cloud Run docling check accepts both 200 and 403 as "up" (confirmed with the user) —
  the service is deliberately IAM-locked (ERP-047) and an external Synthetic Monitoring probe
  has no GCP identity token, so 403 there means reachable, not broken. The alternative (loosening
  Cloud Run's auth for a real 200) was explicitly declined.
- ERP-090's Modal Ollama check runs every 60 minutes, not 5 — a shorter interval would repeatedly
  wake Modal's scale-to-zero GPU container, directly undoing ERP-091's decision not to pay for
  always-warm infra. Confirmed live: a bare `GET /` cold start costs ~10.7s (cheap relative to a
  full generation's 52-59s, since it doesn't load the model into GPU memory).
- Redeployed the live VM mid-session (user's explicit go-ahead) so `/health` (ERP-091) would
  exist in production before ERP-090's check could target it — this was also the first time
  ERP-091's UX-mitigation code and ERP-095's cross-device job-sync fix reached production.

## Implementation Summary

- `app/core/router.py`: `/health` extended to also check Redis (`EMBEDDING_REDIS_URL`), covering
  ERP-090's Neon+Upstash reachability AC in one endpoint. Merged via PR #63.
- Grafana Cloud: new Postgres data source (Neon, created manually via the UI — embedding the
  live DB password in an API call was blocked by this environment's safety classifier), new
  Grafana service account token (dashboard management, distinct from the existing
  telemetry-only Cloud API Key) and a new Synthetic Monitoring access token (its own separate
  token type, needed a one-time "Initialize plugin" UI step first).
- 13 new dashboard panels total across the three tickets (4 eval-history, 4 latency-breakdown,
  1 row + up/down stat + response-time timeseries for service status, plus 3 row headers),
  committed as `deploy/grafana/dashboards/ai-platforms-service-observability.json` — this
  project's first dashboard-as-code artifact. `deploy/grafana/README.md` documents the manual
  (not automated) sync-back workflow.
- 3 Synthetic Monitoring checks live: `vm-app-health`, `cloud-run-docling-health` (both 5 min),
  `modal-ollama-health` (60 min).

## Blockers / Known Gaps

- `evaluation_runs`/`generation_evaluation_runs` are empty in production (ERP-087's panels are
  query-verified, not yet visually verified with real data) — the evaluation harnesses have only
  ever run against local/CI-ephemeral Postgres. Running one against production Neon would also
  call the live paid Modal endpoint, deliberately not done in this session.
- `probe_success=0` for a failing check can lag noticeably behind Synthetic Monitoring's own
  faster Reachability/Uptime display when propagating into the `grafanacloud-prom` Prometheus
  datasource the dashboard panel queries (observed directly: 10+ minutes for a throwaway broken
  check, vs. ~5-7 minutes for the three real checks' first successful data). Worth checking the
  SM Checks page directly during a suspected live outage if the dashboard hasn't updated yet.
- ERP-096 (tab-switch bug, needs live browser access) and ERP-095's two-device live verification
  remain open from prior sessions.

## Next Steps

- Optionally run the evaluation harnesses against production (from the VM, using its own `.env`)
  to seed real data into ERP-087's panels — a deliberate choice, not done automatically here
  since it costs real Modal/LLM usage.
- Consider a budget/cost check on Modal given the new 60-minute health check now adds a small,
  real, recurring cold-start cost (rough order: a few dollars/month) on top of normal usage.
