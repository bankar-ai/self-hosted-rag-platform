# Session — Fix Model-Tag, Hardware-Sizing, and Eval-Judge Bugs (ERP-034/035/036)

Date: 2026-09-08
Tickets Touched: ERP-034, ERP-035, ERP-036

## Decisions

- For ERP-034, chose "fail fast via an operator-run tool" over a FastAPI startup hook -- a startup
  hook would add a live-Ollama network dependency to every app boot and to test collection, which
  doesn't fit this repo's pattern of tests never hitting real Ollama. Implemented both an explicit
  log line (the "(b)" option) and a standalone check tool (the "(a)" option) rather than picking
  only one, since they're cheap together and serve different moments (log line: what's configured
  right now; tool: a deployable pre-flight gate).
- For ERP-036, did both retry-once and a distinct `None` sentinel rather than picking one -- a
  bounded retry recovers a real score when the failure was transient LLM noise, and the sentinel
  still protects the mean/summary when it isn't.

## Implementation Summary

- New `app/core/model_check.py`: `list_available_models`, `check_model_available`,
  `verify_model_or_raise` -- checks a model tag against `ollama.Client(host).list()`.
- New `app/core/check_models.py`: CLI (`uv run python -m app.core.check_models`) checking both
  `GenerationSettings.model` and `EmbeddingSettings.model`, exit(1) with an actionable message if
  either is missing.
- `app/generation/client.py`/`app/embedding/client.py`: added an INFO-level log line at client
  construction naming the configured model/host and pointing at the check tool.
- `.env.example`: documented `GENERATION_MODEL`/`EMBEDDING_MODEL` with Ollama's exact-tag
  resolution behavior explained.
- New `docs/deployment.md`: model-tag-matching operational note (ERP-034) plus hardware/VRAM
  sizing guidance per model actually referenced by this repo, including the observed
  non-determinism of Ollama OOM failures under concurrent system load (ERP-035); cross-references
  `D:\github-projects\infrastructure-options.md`.
- `app/evaluation/judges.py`: `_parse_score` returns `float | None` (was `float`, silently `0.0`);
  new `_score_with_retry` retries once per metric before giving up; `OllamaLLMClientJudge.score()`
  uses it for all three metrics.
- `app/evaluation/schemas.py`: `GenerationScores`/`GenerationQueryResult` fields are now
  `float | None`; `GenerationEvaluationSummary` gained three `*_parse_failures: int = 0` fields.
- `app/evaluation/generation_runner.py`: new `_mean_and_failures` helper computes each mean over
  only non-`None` scores and counts the `None`s; wired into `GenerationEvaluationSummary`
  construction.
- `app/evaluation/generation_run.py`: CLI prints an explicit parse-failure-count line and `N/A`
  (not a crash) for any per-query `None`.
- Tests: `tests/core/test_model_check.py`, `tests/core/test_check_models_cli.py` (new);
  `tests/evaluation/test_judges.py` (replaced the test asserting the old, wrong `0.0`-on-failure
  behavior with retry-succeeds and retry-still-fails cases); `tests/evaluation/test_generation_runner.py`
  (new test: a judge that always fails one metric doesn't drag that metric's mean toward 0, and the
  failure count matches).
- Brought Docker Desktop and the `postgres`/`redis` compose services up (they were down at the
  start of this session) to run the full suite against real infra rather than only fakes.

## Blockers

None. ERP-037 (live deployment) remains the next piece of work and no longer has an unmet
dependency -- ERP-034 and ERP-035 are both Done.

## Next Steps

- Start ERP-037: live-verify GCP `e2-micro` (or fall back to Render Starter), wire up Neon/Upstash/
  Modal, deploy, smoke-test, document teardown.
- Re-run ERP-029's retrieval-quality evaluation harness against live Ollama now that it's reachable
  (still unconfirmed since ERP-031's per-owner FAISS partitioning).
