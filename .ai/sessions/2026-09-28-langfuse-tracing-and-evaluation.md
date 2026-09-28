# Session — Langfuse tracing and automated evaluation

Date: 2026-09-28
Tickets Touched: ERP-112

## Decisions

- Scope of ERP-112 confirmed with the user before starting: live `/generation/query` traffic
  only. Wrapping the offline `app/evaluation/` (ERP-029/030) runs in Langfuse is explicitly
  deferred, not built.
- Researched current Langfuse SDK state before implementing (per `CLAUDE.md`'s
  research-before-recommending rule): the ticket's "v3" assumption was already outdated — current
  is v4, OTel-native. Confirmed the coexistence mechanism with this repo's existing OTel setup:
  Langfuse attaches its own span processor to whatever global `TracerProvider` already exists,
  as long as Langfuse initializes *after* `app/core/telemetry.py`'s `configure_telemetry()` has
  already called `set_tracer_provider()`.
- Langfuse Cloud account created: Hobby (free) tier, US data region (matches the live VM's
  `us-central1-a`), "Enable AI powered features" (AWS Bedrock data sharing) left off since
  unrelated to this ticket's scope. Project renamed from the default "My Project" to
  `self-hosted-rag-platform` for consistency with every other cross-system name this app already
  uses (Prometheus job name, Grafana dashboard title, PyPI package name).
- For the separate, same-session ask of adding automated scoring (Langfuse Evaluators): chose the
  existing free Modal-hosted Ollama endpoint (`gemma3:4b`) as the judge LLM, not OpenRouter and
  not local Ollama. Local Ollama was ruled out for a real reason, not just cost: Langfuse Cloud's
  Evaluators run on Langfuse's own servers, so a `localhost` endpoint is categorically
  unreachable from there, independent of any cost question. Cold-start latency (the same
  scale-to-zero behavior `ERP-091`'s `ping()` trick works around for generation) was judged a
  non-issue for scoring specifically, since it's asynchronous background work with no user
  waiting on it.
- Sampling rate for the Evaluator set to 100% (not throttled), since current real traffic (5-20
  sporadic test users) is nowhere near the Hobby tier's 50k-units/month ceiling (units = traces +
  observations + scores; current-state.md's estimate is roughly 25k generation calls/month
  headroom even before adding scores).

## Implementation Summary

**Code (PR #84, merged to `develop`)**:
- New `app/generation/tracing.py`: `trace_generation()` context manager, a Langfuse "generation"
  observation wrapper that degrades to a no-op (not an exception) whenever
  `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` are unset or the SDK fails to initialize — same
  never-load-bearing philosophy as this repo's Redis caches (ADR-003).
- `app/generation/client.py`: both `OllamaLLMClient` and `OpenRouterLLMClient`'s
  `generate`/`generate_stream` wrapped in `trace_generation(...)`, nested inside the existing
  `llm.generate` OTel span. Non-streaming `generate()` also captures token usage where the
  provider's response surfaces it; streaming does not (documented gap, not solved this session).
- New dependency: `langfuse` v4.15.6, added via `uv add` after explicit user approval per
  `CLAUDE.md`'s dependency policy.
- `.env.example` and `docs/deployment.md` gained new documented sections for the optional
  `LANGFUSE_*` env vars and where to view traces.
- TDD throughout: `tests/generation/test_tracing.py` (new, 100% coverage — no-op path, successful
  init, and the "init raises → degrades to None" path all independently tested) plus new wiring
  tests in `tests/generation/test_client.py` covering all four call sites. Full suite: 614
  passed, 95.77% coverage, ruff/mypy clean.
- Live-verified against real infrastructure, not just unit tests: called
  `OllamaLLMClient.generate()` against real local Ollama (`gemma3:4b`), then confirmed via
  Langfuse's public API (`GET /api/public/v2/observations`) that a `GENERATION`-type observation
  named `llm.generate` actually landed in the Langfuse Cloud project — prompt, output, model, and
  token usage (`{"input": 35, "output": 8}`) all matched exactly what the code should produce.

**Langfuse-UI-only config, no code (no PR — nothing to review in git)**:
- LLM Connection `modal-ollama` (adapter `openai`, base URL
  `https://pankajkumar-bankar--self-hosted-rag-platform-ollama-olla-6f6aea.modal.run/v1`, custom
  model `gemma3:4b`), verified working via Langfuse's Playground before use.
- Evaluator `response-quality` (LLM-as-a-judge, scores grounding/hallucination 0-1, with a
  reasoning field so a human can verify the judge's call without re-reading the full trace),
  tested against the one real trace before activating (score 1, correct reasoning, ~100s due to
  a cold Modal container — expected and harmless for background scoring).
- Rule attached: reuses the evaluator's own root-observation filter, 100% sampling, past
  observations backfilled once at setup.
- Full setup recorded in `docs/deployment.md`'s new "Automated scoring (Langfuse Evaluators)"
  subsection, since this configuration lives only in Langfuse's project settings and would
  otherwise be invisible to anyone reading this repo.

## Blockers

None.

## Next Steps

- OpenRouter's $5 pay-as-you-go credit is now live on the account that was blocked at $0.00
  during `ERP-111`'s incident (same session, reported by the user just as this session was
  wrapping up) — next immediate task is re-verifying `GENERATION_PROVIDER=openrouter` actually
  works end-to-end now that the account is funded, both locally and (once confirmed) re-enabling
  it on the live VM (currently reverted to `ollama` since `ERP-111`).
- Possible follow-ups, not yet tickets: wrap `app/evaluation/` (ERP-029/030) offline runs in
  Langfuse too; add streaming token-usage capture (needs provider-specific `stream_options`);
  cross-project OpenRouter API key separation once the Agentic AI repo exists (per the
  key-per-project recommendation discussed but not yet written into
  `D:\github-projects\infrastructure-options.md`).
