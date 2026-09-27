# Session — Production Sampling and Scoring of Live Traffic (ERP-097)

Date: 2026-09-26
Tickets Touched: ERP-097

## Decisions

Preceded by a latency investigation (same day, not itself a ticket): pulled real Grafana Cloud
trace/metric data showing chat latency is dominated by Modal cold starts (both the embedding and
generation models share one lazily-spun-up container), not by gemma3:4b being inherently slow.
That finding is tracked for a later discussion (the user has ideas involving their existing
Claude Pro/ChatGPT Go subscriptions — confirmed via research that neither includes API access,
so any such move would need separate API billing).

ERP-097 itself: resolved all four of its open decisions (sample rate, trigger mechanism, storage
shape, surfacing) rather than leaving them for a follow-up — see the ticket's own Resolution
section for the full reasoning. Summary: score every unsampled message (not a percentage sample),
manual CLI trigger (never touches the live chat request path), a new dedicated
`production_sample_scores` table (not reusing `generation_evaluation_runs`), aggregate-only on
the Grafana dashboard (no new API endpoint).

## Implementation Summary

Built via TDD in the same worktree as ERP-098/ERP-099 (`erp-098-099-citation-job-sync-fixes`,
new branch `erp-097-production-sampling`):

- New `production_sample_scores` table (migration `6412377c47e5`).
- `app/ingestion/repository.py` gained `get_chunks_by_ids`.
- `app/evaluation/repository.py` gained `get_unsampled_assistant_messages`,
  `get_preceding_user_message`, `save_production_sample_score`.
- `app/evaluation/production_sampling.py`'s `run_production_sampling` orchestrates scoring,
  reusing the existing `GenerationJudge` interface unchanged.
- `app/evaluation/production_sample_run.py`: CLI entrypoint, same pattern as the golden-dataset
  harnesses.
- Refactored the shared "mean excluding `None`" logic out of `generation_runner.py` into
  `app/evaluation/metrics.py` (`mean_excluding_none`) so both scoring paths share it instead of
  duplicating it.
- New Grafana dashboard row ("Production Sampling - Live Traffic (ERP-097)"), pushed via the
  Grafana HTTP API and re-synced to `deploy/grafana/dashboards/ai-platforms-service-observability.json`.

**Verified (pre-deploy)**: backend 572 passed (was 558), ruff/mypy clean. PR #67 merged to
`develop`, then promoted to `main` via PR #68 (bringing ERP-098/ERP-099/ERP-097 together).

## Live Deployment and Verification

Both PR merges were blocked by the environment's auto-mode permission classifier (same recurring
pattern as prior sessions) — the user merged both directly.

Deploying hit a real, previously-undocumented gotcha: a non-interactive
`gcloud compute ssh --command=...` invocation of `alembic upgrade head` connected to
`127.0.0.1:5432` instead of live Neon — `DATABASE_URL` was never loaded, because a bare shell
command doesn't source `~/app/.env` the way systemd's `EnvironmentFile=` does. Layered on top of
this, PowerShell's own variable interpolation and `gcloud.cmd`'s batch-file argument parsing both
mangled attempts to pass the already-documented `.env`-parsing workaround (the `&` in
`DATABASE_URL` breaking naive `source .env`) directly on the command line. Resolved by writing
the migration/sampling commands to local script files and `gcloud compute scp`-ing them to the
VM, then invoking each with a trivial `bash script.sh` command line — sidesteps all three layers
of quoting/escaping entirely. Worth remembering for any future non-interactive migration/script
run against this VM.

Full live run: migration applied cleanly to Neon (confirmed via a direct query through the
Grafana Postgres data source), app redeployed and restarted (`200` on `/docs`), then
`production_sample_run --judge ollama` run against all 75 real `conversation_messages` assistant
rows that existed (two batches, `--limit 15` then `--limit 60`, since 75 exceeded the default
`--limit 50`). Result: 9 scored, 66 skipped (mostly older messages from throwaway test accounts
whose documents were since deleted — a real, expected case of the "tolerate a deleted citation"
behavior, not a bug). Real scores from actual production traffic: **Faithfulness 0.950, Answer
Relevancy 0.789, Context Precision 0.933**.

**A real, useful finding from the live run itself**: the 60-message batch (9 of which needed
actual judge-LLM scoring) took roughly an hour to complete. The process was confirmed alive via
`ps aux` multiple times throughout (near-zero CPU, blocked on network I/O), not hung — this
matches the same-day latency investigation's finding that gemma3:4b generation calls via Modal
can take 80-250+ seconds even when not a full cold start. The judge-scoring pipeline inherits the
exact same latency characteristic already flagged for the live chat UI. `OllamaLLMClient.generate`
also has no request timeout configured at all — worth a future look if a judge call ever needs a
hard upper bound instead of waiting indefinitely.

Temporary scripts used for the deploy (`run_migration.sh`, `run_sampling.sh`) were uploaded to
and removed from the VM within this session — never committed to the repo, consistent with the
project's standing rule that VM-targeting scripts live outside version control entirely.

## Blockers

None remaining. Both PR merges needed the user's direct action (classifier block).

## Next Steps

- The user has a separate, not-yet-detailed idea involving their Claude Pro/ChatGPT Go
  subscriptions for the generation/embedding latency problem — confirmed neither includes API
  access, so this becomes a "use the actual API, billed separately" conversation whenever the
  user is ready to pick it up.
- Consider adding a request timeout to `OllamaLLMClient.generate` (no ticket yet — not built,
  just noted here since it surfaced directly from this session's live run).
- Re-run `production_sample_run` periodically (still manual-only, by design) as new production
  traffic accumulates, so the dashboard keeps reflecting recent activity, not just this one
  seeding pass.
