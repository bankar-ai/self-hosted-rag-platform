# Session — ERP-091 latency UX mitigation, Grafana dashboard-token plan

Date: 2026-09-25
Tickets Touched: ERP-091 (Done), ERP-087/089/090 (still Backlog, unblocked with a plan)

## Decisions

- **ERP-091**: UX mitigation over paid always-warm infra. Presented the user real cost numbers
  (Modal `min_containers=1` ~$430/mo, Cloud Run `min-instances=1` ~$175-200/mo, a Neon keep-alive
  ping already exceeding its free 100 CU-hrs/month allowance on its own) against ADR-008's
  explicit "bounded 2-3 month test, free-tier, 5-20 users" scope. User confirmed: no paid infra,
  UX mitigation only.
- **Grafana dashboards (ERP-087/089/090)**: user chose to create a new Grafana Cloud *service
  account* token (Editor role, created inside the `microstarfish1843` stack's own
  Administration UI) rather than hand-pasting dashboard JSON. This is a different credential
  from the existing `set:alloy-data-write` Cloud API Key (org-level, telemetry-ingestion only,
  cannot manage dashboards) — confirmed via a live web search before recommending it, per
  CLAUDE.md's research-before-recommending rule for fast-moving tooling. Once the user hands
  over the token, the plan is to build all three dashboards via the Grafana HTTP API and commit
  the resulting JSON to `deploy/grafana/dashboards/` as dashboard-as-code (a real improvement —
  today's one existing dashboard exists only in the Grafana UI, not version controlled).

## Implementation Summary

ERP-091, fully implemented and merged (PR #62):
- Backend: `app/core/router.py` (new, `GET /health`), `OllamaLLMClient.ping()`
  (`app/generation/client.py`), `warmup_llm()` (`app/generation/service.py`),
  `POST /generation/warmup` (`app/generation/router.py`).
- Frontend: `LoginPage.tsx` (submitting state + slow-hint + `/health` pre-warm ping),
  `ChatPage.tsx` (cold-start hint on the typing indicator + `/generation/warmup` ping — this
  page had zero prior test coverage, so `ChatPage.test.tsx` is new), `DocumentsPage.tsx`/
  `documentsStore.ts` (`startedAt` field, processing-stuck hint).
- Verified: backend 553 passed, frontend 84 passed, `tsc -b`/`oxlint`/`ruff`/`mypy --strict`
  clean. Not live-verified against a real cold start on the deployed app.

No code written yet for ERP-087/089/090 — this session only unblocked the credential question.

## Blockers

- ERP-087/089/090 still waiting on the user to actually create and hand over the new Grafana
  service account token.
- ERP-091's hints are untested against a real live cold start (only unit/integration-tested
  against mocked/simulated delays).
- ERP-096 (tab-switch bug) and ERP-095's two-device live check remain from the prior session,
  still open.

## Next Steps

- Once the Grafana token is in hand: build ERP-087 (eval-run history, Postgres data source),
  ERP-089 (latency-breakdown panel, reuses existing OTel telemetry), ERP-090 (all-services
  up/down — also needs a `/health`-equivalent check on Modal Ollama and confirming Cloud Run
  docling's existing one, plus enabling Grafana Cloud Synthetic Monitoring, a separate product
  from regular dashboards that may need its own one-time activation step).
- Recommend a live check of ERP-091's hints during an actual cold start once deployed.
