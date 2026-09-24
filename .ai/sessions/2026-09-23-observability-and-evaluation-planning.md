# Session — Docling OOM Investigation, Mobile Bug Corroboration, Observability/Evaluation Planning

Date: 2026-09-23
Tickets Touched: ERP-085, ERP-086, ERP-087, ERP-088, ERP-089, ERP-090

## Decisions

- Investigate-and-log this session; no implementation. Fix/build work explicitly deferred to a
  future session once priorities are picked.
- For the all-services status dashboard, use Grafana Cloud Synthetic Monitoring (already the
  platform's existing observability tool) over a third-party alternative like UptimeRobot, to
  keep everything in one dashboard per the user's actual ask.

## Implementation Summary

No code changed. Docs/tickets only:

- **ERP-085** updated: the mobile header-wrap bug was independently reproduced by two more real
  external users (screenshots), raising its priority.
- **ERP-086** (new): root-caused a live 503 on `POST /ingestion/pdf` to the Cloud Run docling
  service being OOM-killed — confirmed directly from GCP Cloud Logging via `gcloud logging read`
  (`superpowers:systematic-debugging` Phase 1), not inferred. The failing document
  (`Group-C-Pre-Test-02-Set-A-30-08-2026-F.PDF`) exceeded the container's `--memory 4Gi` limit.
  Two occurrences ~12 min apart. Fix options documented (bump memory / cap document size / surface
  a friendlier error), none implemented yet.
- **ERP-087** (new): dashboard for existing golden-dataset evaluation run history
  (`evaluation_runs`/`generation_evaluation_runs` in Postgres). Confirmed via live web search
  that Grafana Cloud's free tier supports a Postgres data source natively, no new cost.
- **ERP-088** (new): design ticket for evaluating live production traffic (not just the fixed
  4-query golden dataset) — inline judging vs. async sampling vs. existing thumbs up/down
  feedback as a signal. Decision deferred, not started.
- **ERP-089** (new): latency-breakdown dashboard panel using telemetry ERP-028/042 already
  collect (`llm.generate`/`embedding.generate`/`faiss.search`/etc. spans, `http_server_duration_milliseconds`)
  but never surfaced as a panel.
- **ERP-090** (new): all-services up/down + performance dashboard. Cloud Run docling and Modal
  Ollama currently emit no telemetry to Grafana Cloud at all (that gap is exactly why ERP-086
  needed direct `gcloud` log access instead of Grafana). Researched free-tier options live:
  Grafana Cloud Synthetic Monitoring (100k API-test executions/month free, same platform,
  recommended) vs. UptimeRobot (50 monitors free, simpler but a separate tool).

## Blockers

None — all four new tickets (ERP-087 through ERP-090) are ready to plan/implement; ERP-086 is
ready to fix.

## Next Steps

- Prioritize among ERP-085 through ERP-090 for the next implementation session.
- ERP-086 (docling OOM) and ERP-085 (mobile bug) are both live user-facing bugs with confirmed
  root causes — likely the highest-priority pair.
- ERP-087/089 are cheap (dashboard/config-only, no new instrumentation) and could be bundled.
- ERP-088 needs a decision before any code is written.
- ERP-090 needs `/health` endpoint coverage checked across services before Synthetic Monitoring
  checks can be configured.
