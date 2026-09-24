# Session — ERP-095 fix, ERP-088 design decision, remaining-backlog triage

Date: 2026-09-24
Tickets Touched: ERP-095, ERP-088, ERP-097, ERP-096, ERP-087, ERP-089, ERP-090, ERP-091

## Decisions

- **ERP-095**: server-authoritative status over documenting-as-expected. The in-memory job
  tracker already had everything needed (`owner_id`, `status`, `filename`, `error`) to answer
  "what's this account's active jobs" account-wide — it just wasn't exposed via an endpoint.
- **ERP-088**: async sampling over inline judging (latency cost, rejected) or feedback-only
  (already-collected but low response rate, kept as a complement not a replacement). Spun off
  as **ERP-097** rather than implemented directly, per the ticket's own AC.
- Triaged the other 5 remaining Backlog tickets and identified two hard blockers rather than
  attempting speculative work against them:
  - ERP-096 needs live browser reproduction (devtools) — no browser-automation tool available
    in this environment.
  - ERP-087/089/090 need Grafana Cloud dashboard-write access — the only stored API token is
    scoped `set:alloy-data-write` (telemetry ingestion only, confirmed by reading it), not
    dashboard management.
  - ERP-091 is explicitly gated on ERP-089 (needs the latency panel first) and on an explicit
    user decision about a paid cost trade-off (`min_containers=1`) — not something to decide
    unilaterally even with broad execution permission.

## Implementation Summary

- `app/ingestion/jobs.py`: new `list_active_jobs(owner_id)`.
- `app/ingestion/schemas.py`: new `JobSummary`/`JobListResponse`.
- `app/ingestion/router.py`: new `GET /ingestion/jobs`.
- `frontend/src/pages/DocumentsPage.tsx`: hydrates in-progress jobs from the new endpoint on
  mount; new `pollingJobIdsRef`/`ensurePolling` guard against double-polling a job this device
  already started.
- `frontend/src/lib/types.ts`: matching `JobSummary`/`JobListResponse` types.
- Tests: `tests/ingestion/test_jobs.py`, `tests/ingestion/test_router.py`,
  `frontend/src/pages/DocumentsPage.test.tsx` (new cases for all of the above).
- `.ai/tickets/ERP-088.md` updated with the Decision section; `.ai/tickets/ERP-097.md` created.
- `.ai/tickets/ERP-095.md` updated with the Decision/Implementation/Testing sections, marked Done.

Merged to `develop` via PR #61 (ERP-095 code); ERP-088/097 doc updates committed directly to
`develop` (docs-only, matching this repo's established pattern for ticket-log commits).

## Blockers

- ERP-096: needs a live device/browser to reproduce per the ticket's own AC — flagged to the
  user rather than guessed at.
- ERP-087/089/090: need either a new Grafana Cloud API token scoped for dashboard management, or
  the user to apply prepared dashboard JSON/queries manually in the Grafana UI.
- ERP-091: needs ERP-089 first, then an explicit user decision on the `min_containers=1` cost
  trade-off before any infra change.

## Next Steps

- Ask the user how they want to proceed on the three blocked areas above (live device access,
  Grafana token/manual application, and whether they want to have the cost-tradeoff conversation
  for ERP-091 now or defer it).
- Recommend a manual two-device check for ERP-095 (upload from one browser, confirm visible
  mid-upload from another) before considering it fully closed end-to-end.
