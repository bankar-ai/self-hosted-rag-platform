# Session — Deployment Readiness Testing and Cross-Project Infrastructure Research

Date: 2026-09-08
Tickets Touched: ERP-034, ERP-035, ERP-036, ERP-037

## Decisions

- Ran the app live against real local Ollama models (not just unit tests with fakes) for the first
  time, specifically to answer "does this actually work / give the right answers when we make it
  live". Found and ticketed three real bugs (ERP-034, ERP-035, ERP-036) that a fully green CI suite
  had completely missed.
- Confirmed live that the RAG pipeline gives faithful, correctly-cited, non-hallucinated answers
  (`gemma3:4b`, faithfulness 1.00 across all 4 golden-dataset queries) — genuine evidence for the
  "will it give the right answers" question, independent of the deployment bugs found.
- Decided the deployment target for making this platform reachable for a bounded 2-3 month test
  window (5-20 users max): GCP's Always Free `e2-micro` VM (permanently free) for app + FAISS
  hosting, pending live verification, with Render Starter (~$7/mo) as fallback; Neon (Postgres),
  Upstash (Redis), and Modal (serverless GPU) already decided with higher confidence. Recorded in
  ADR-008 and the new shared `D:\github-projects\infrastructure-options.md`.
- Decided this infra research is genuinely cross-project (also relevant to the future Agentic AI,
  PEFT/LoRA, and LLMOps repos named in `docs/architecture.md`), so it lives in a shared sibling
  file rather than being duplicated per-repo.
- Scoped the future Agentic AI project down to single-agent learning only (not multi-agent) for
  now, using OpenRouter's free tier (not Claude API, not self-hosted) for model access, and
  LangGraph as the framework if/when that project starts — deferred, not started this session.

## Implementation Summary

- Live-tested `POST /generation/query`'s dependency chain directly against a real local Ollama
  install (`nomic-embed-text`, `gemma3:4b`, `qwen3:8b` all installed). Found:
  1. `GenerationSettings.model` defaults to `"qwen3"`, which Ollama cannot resolve to the installed
     `qwen3:8b` tag (`{"error": "model 'qwen3' not found"}`) -- ticketed as ERP-034.
  2. `qwen3:8b` intermittently OOM'd (both GPU-VRAM and CPU-pinned-buffer failure modes observed)
     under real system RAM pressure (as low as ~2.1GB free of 16GB total) caused by concurrent
     background agent workloads -- not a hardware defect (the same model loaded fine once RAM
     pressure eased) but an undocumented sizing gap -- ticketed as ERP-035.
  3. `OllamaLLMClientJudge` (ERP-030's fallback generation-quality judge) silently scores a metric
     `0.0` when the judge LLM's response can't be parsed, indistinguishable from a genuinely bad
     answer -- ticketed as ERP-036.
- Ran `app.evaluation.generation_run --judge ollama` end-to-end against real Ollama + real Postgres
  (after fast-forwarding the local `develop` checkout and running `alembic upgrade head`, which had
  drifted behind the already-merged PR #31/#32 work) using `gemma3:4b`: 4/4 queries answered with
  faithfulness 1.00; two of the four answer_relevancy/context_precision scores were 0.00 due to the
  ERP-036 parse-failure bug, not genuinely bad answers.
- Checked and stopped the leftover `agent-a64f0624027ccdcfd-postgres-1`/`-redis-1` Docker containers
  (running 11+ hours from earlier agent work) once testing was done.
- Researched (via live web search, dated 2026-09-08) hosting/compute options across Render, GCP,
  Azure, AWS, Railway, Fly.io (Postgres: Neon, Supabase; Redis: Upstash; GPU serving: Modal, RunPod,
  Hugging Face Spaces ZeroGPU, Azure GPU VMs, AirLLM; GPU training: Kaggle, Colab, Lightning AI; LLM
  API: OpenRouter; LLMOps: Langfuse Cloud vs. self-host vs. custom-build) and consolidated findings
  into `D:\github-projects\infrastructure-options.md`.
- Wrote ADR-008 recording the shared-doc decision, ERP-037 to track live-verifying and executing the
  chosen deployment, and a pointer in `docs/architecture.md` to the shared infra doc.

## Blockers

None currently blocking -- ERP-034/035/036 are ticketed but unfixed (Backlog), and ERP-037's live
verification of the GCP `e2-micro` option has not been attempted yet.

## Next Steps

- Fix ERP-034 (default generation model) and ERP-035 (hardware sizing doc) before attempting
  ERP-037's live deployment, so the deployed instance doesn't hit the same failures found locally.
- Fix ERP-036 (eval harness parse-failure handling) so future evaluation numbers can be trusted.
- Execute ERP-037: live-verify GCP `e2-micro` (or fall back to Render Starter), wire up Neon/Upstash/
  Modal, deploy, smoke-test at ~10-20 concurrent requests, document teardown steps.
- When the Agentic AI / PEFT / LLMOps projects actually start, reference
  `D:\github-projects\infrastructure-options.md` rather than re-deriving hosting research from
  scratch.
