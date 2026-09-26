# deploy/grafana/

Dashboard-as-code for the "AI Platforms — Service Observability" Grafana Cloud dashboard
(`microstarfish1843` stack, uid `pav87rr`). Added in ERP-087 (2026-09-25) -- before this, the
dashboard existed only in the Grafana UI with no version-controlled record of its panels/queries.

## Files

- `dashboards/ai-platforms-service-observability.json` — the dashboard's full JSON model, as
  returned by `GET /api/dashboards/uid/pav87rr`'s `.dashboard` field. Committed here as a
  point-in-time snapshot of what's live, not automatically synced.

## How this is actually applied

There is no CI/CD pipeline pushing this file to Grafana -- changes are made directly against the
Grafana HTTP API (or the UI) first, then this file is re-fetched and committed to keep the repo
in sync. To update a panel:

1. Edit it live in the Grafana UI (or via `POST /api/dashboards/db` with a modified JSON body),
   using the token described below.
2. `GET /api/dashboards/uid/pav87rr`, take the `.dashboard` field, and overwrite this file with
   it (pretty-printed, UTF-8, `ensure_ascii=False` -- see the ERP-087 session notes for a real
   mojibake bug this caused once when re-encoded through a misconfigured Python invocation on
   Windows; always verify panel titles round-trip cleanly before committing).
3. Commit the updated JSON with a message naming which ticket/change it reflects.

This one-way, manual sync is a deliberate starting point, not a design decision to revisit
lightly -- a real provisioning pipeline (Terraform's `grafana` provider, or Grafana's own
file-based provisioning) would be the next step if this dashboard starts changing often enough
that manual re-sync becomes a real burden.

## Data sources referenced

- `grafanacloud-logs` (Loki), `grafanacloud-traces` (Tempo) -- pre-existing, provisioned as part
  of the Grafana Cloud stack itself.
- `efza4kyumru9sb`, a **PostgreSQL** data source pointing at this project's live Neon database
  (ERP-087), added manually via the Grafana UI (Connections → Add new connection → PostgreSQL)
  rather than via the API, since creating it programmatically would have meant sending the live
  Neon password through this session's own tool calls -- blocked by the environment's safety
  classifier, and not worth working around for a one-time setup step. See
  `C:\Users\Pankaj\.credentials\self-hosted-rag-platform-credentials.md` (outside this repo) for
  the connection details and the dashboard-management API token used to build these panels.

## Credentials

The Grafana **service account token** used to build/update these dashboards via the API is
separate from the existing `set:alloy-data-write` Cloud API Key used for OTLP export (that one
can only ingest telemetry, not manage dashboards). Both live in the credentials file above, never
in this repo.
