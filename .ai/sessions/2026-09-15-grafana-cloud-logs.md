# Session — Grafana Cloud Application Logs (ERP-039)

Date: 2026-09-15
Tickets Touched: ERP-039

## Decisions

- Chose the in-process OTLP log exporter over a log-shipping agent (Alloy/Promtail), the explicit
  decision ERP-039's acceptance criteria required backing with **measured** evidence: deployed and
  compared live VM memory before vs after (uvicorn RSS, system-wide available memory) and found no
  detectable difference. A separate agent would have been another always-running process competing
  for the same tight RAM budget that already required ERP-037's lazy-docling-import fix just to fit
  the app itself.
- Reused ERP-038's existing Grafana Cloud token and env vars entirely -- no new token, no new env
  var, since the token's `set:alloy-data-write` scope already covers `logs:write`.

## Implementation Summary

- `app/core/logging_config.py`: `configure_logging()` now also builds an OTel `LoggerProvider` +
  `BatchLogRecordProcessor` + `LoggingHandler`, attached alongside the existing stdout handler. New
  `_build_log_exporter()` mirrors `app.core.telemetry._build_span_exporter()`'s protocol selection.
  OTLP setup failure is caught and swallowed, matching every other observability integration's
  never-load-bearing philosophy in this repo.
- `tests/core/test_logging_config.py`: added tests mirroring `test_telemetry.py`'s
  degrade-gracefully/unreachable-endpoint pattern, plus a protocol-selection test -- reached 100%
  coverage on `logging_config.py`.
- Committed to `develop` (commit `5887d64`) and pushed; deployed to the live VM: `git pull` +
  `uv sync` + `systemctl restart`.
- `docs/deployment.md`: new "Application logs (Grafana Cloud)" section.
- `D:\github-projects\gcp-deployment-tracker.md`: added a logs row under the traces entry.
- `C:\Users\Pankaj\.credentials\self-hosted-rag-platform-credentials.md`: noted the same token now
  also covers logs.
- Verified memory impact live: captured `free -h` + `ps aux` for the uvicorn process both
  immediately before and ~30s after deploying, under real request traffic -- RSS and available
  memory both within noise of the pre-change baseline.
- Verified end-to-end with real traffic: registered a second throwaway test user
  (`erp039-verify@example.com`, also left in the live DB, same known gap as ERP-038's), ran a real
  `POST /retrieval/query`, and confirmed in Grafana Cloud's Explore -> Loki
  (`grafanacloud-microstarfish1843-logs`, filtered by `service_name`) real log lines with
  `trace_id`/`span_id` fields and a working "Links -> traceID" pivot straight to the matching trace
  in Tempo. Logged the test user's session out afterward.
- Verified locally: an unrelated environment issue surfaced along the way -- the local
  docker-compose Postgres/Redis/Jaeger containers had stopped (exited 2 days prior, likely a
  Docker Desktop restart), which made `pytest`'s session-scoped DB fixture hang indefinitely
  (confirmed via `faulthandler.dump_traceback_later`, not guessed). Restarting the containers
  (`docker compose up -d postgres redis jaeger`) resolved it -- unrelated to the code change itself.
  After that: ruff/mypy clean, full suite 372 passed, 96.59% coverage.

## Blockers

None — ERP-039 is fully resolved. Both ERP-038 and ERP-039 are now Done.

## Next Steps

- Metrics export to Grafana Cloud is not yet ticketed (pull-based Prometheus exporter has nothing
  to scrape it on the VM; needs a remote-write agent or a switch to push-based OTLP metrics).
- Consider a delete-user path (admin or self-service) -- two throwaway test user rows now sit in
  the live database with no way to remove them via the API.
