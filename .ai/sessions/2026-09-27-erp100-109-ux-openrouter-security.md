# Session — Chat UX Batch, OpenRouter Generation Provider, Security Review

Date: 2026-09-27
Tickets Touched: ERP-100, ERP-101, ERP-102, ERP-104, ERP-105, ERP-106, ERP-107 (filed only),
ERP-108 (filed only), ERP-109 (filed only), ERP-103 (filed only, untouched)

## Decisions

Continued directly from the 2026-09-26 session (ERP-097 production sampling + live latency
investigation). User asked for a prioritized plan across the open backlog before starting;
agreed order: ERP-106 (OpenRouter) → ERP-100/101/102 (chat UX batch) → ERP-105 → ERP-104, with
ERP-103 deliberately left for later (explicitly low urgency).

**ERP-106 scope decision**: move only the **generation** provider to OpenRouter, not embeddings
or the evaluation harnesses — both explicitly out of scope per the user ("only thing matters
here is generation and embedding" was later clarified to mean the live chat path, not the
evaluation harness, whose own runtime doesn't matter since it's background/batch work never
touching a real user).

**ERP-104 scope decision**: investigated before implementing (per its own AC) and found that
"source document" capture was free (citations already resolve to chunk records with
`source_filename`), but "retrieval settings" (rerank/expand_sections/document scope) are **not
persisted anywhere at all** — capturing those needs a new column on `conversation_messages` and
a hot-path write. Split that half out honestly as ERP-107 rather than bundling it in under time
pressure.

**Security review (user-initiated) surfaced two real gaps**, filed as tickets rather than built
immediately since they're consequential/cost-and-risk decisions: no rate limiting anywhere on
generation/retrieval (ERP-108, direct cost-exposure vector, priority #1 of the two), and no
output-side check to catch an ERP-058 prompt-injection-mitigation failure (ERP-109, chosen
approach is a cheap deterministic non-LLM post-generation check, logged not blocked, to avoid
adding an LLM-judge call that would fight the very latency problem this project just spent two
sessions fixing).

## Implementation Summary

All built via TDD in the same worktree (`erp-098-099-citation-job-sync-fixes`), one branch/PR
per ticket group:

- **ERP-106**: `GenerationSettings.provider` (`"ollama"` default, unchanged; `"openrouter"` new).
  New `OpenRouterLLMClient` (OpenAI-compatible via `httpx`, no new dependency) implements the
  existing `LLMClient` protocol including SSE streaming. New `get_default_llm_client` factory
  centralizes provider selection — all 7 previous `OllamaLLMClient(settings)` call sites in
  `app/generation/service.py` now go through it. `LLMClient` protocol gained `ping()` (both
  implementations already had it; OpenRouter's is a no-op — no cold start to hide).
- **ERP-100**: swapped which `CopyButton` gets which `getText`/label in `ChatPage.tsx` — question
  bubble now copies only the question, answer bubble copies the Q&A pair (was reversed).
- **ERP-101**: `Sidebar.tsx` gained "Select all"/"Deselect all" text buttons next to the document
  checklist, reusing the existing `deselectedDocumentIds` state (no parallel state).
- **ERP-102**: a floating "Jump to latest" button appears via a scroll-position `onScroll`
  handler once the user scrolls away from the bottom; existing ERP-064 auto-scroll-on-new-message
  is untouched.
- **ERP-105**: `run_production_sampling` now commits after every message instead of once at the
  end — a crash partway through only loses the in-flight message.
- **ERP-104**: `production_sample_scores` gained `source_documents` (deduplicated filenames from
  resolved citations), populated from the existing chunk lookup, no new query.

**Verified**: backend 579 passed (was 572 at session start), frontend 93 passed (was 86),
ruff/mypy/tsc/oxlint all clean throughout. Five PRs (#71-#75, one per ticket group) merged to
`develop`, then a single promotion PR (#75... actually the promotion was PR #75 itself — see Git
History below) to `main`, deployed live: migration applied, VM restarted, `200` confirmed.

A sixth PR (#76) filed ERP-107/108/109 as docs-only tickets (no code).

## Live Deployment — OpenRouter Debugging Saga

Deployed ERP-106 live (config-only, safe — default provider unchanged) as part of the same
promotion. Switching it *on* (`GENERATION_PROVIDER=openrouter`) surfaced a real multi-step
troubleshooting sequence, useful to have on record:

1. Wired the user's real OpenRouter API key into `~/app/.env` (via the same script-file-upload
   workaround from the 2026-09-26 session — a non-interactive SSH command still doesn't source
   `.env` cleanly, and PowerShell/`gcloud.cmd`'s own quoting still mangles inline attempts).
2. First live generation test hit a genuine empty-retrieval short-circuit (the test account had
   no documents) — cost nothing to diagnose, just needed a real ingested document to actually
   exercise the LLM call. Uploaded a hand-crafted minimal PDF first; it parsed to zero chunks
   (malformed xref/stream offsets) — regenerated a real one via the project's own PyMuPDF
   (`fitz`) dependency instead of hand-rolling PDF bytes.
2. Real generation call reached OpenRouter correctly (confirmed via VM logs: right provider,
   right model `google/gemma-3-27b-it`) but got `402 Payment Required` — the account's $1
   signup credit didn't cover a paid (non-`:free`) model.
3. User added $5 credit; **still** 402. Diagnosed via OpenRouter's own API
   (`GET /api/v1/credits` → `total_credits: 0`, `GET /api/v1/key` → `is_free_tier: true`) that
   the purchase hadn't actually settled — a screenshot of the Credits page confirmed `$0.00
   available` with a pending autopay/bank-debit text message dated the next day (2026-09-28).
   Clarified for the user: the `$100` figure shown at key creation is a per-key *spending
   ceiling* (a leak-safety cap), completely separate from *actual available balance*, which is
   what was genuinely still $0.
4. Left in a clean, known-good state pending the payment clearing: code deployed, default
   provider still `"ollama"` in practice conceptually but actually **already switched to
   `"openrouter"` in the live `.env`** — so once the payment clears, no further deploy step is
   needed, a real generation request will just start working. Test document (chunk with "the
   secret code word is banana split supreme") was cleaned up / re-uploaded/re-cleaned during
   testing; final state has that account's test data mostly cleaned but worth a check next
   session (see Next Steps).

**Security note from this same stretch**: the real OpenRouter API key ended up in this chat
conversation's own transcript (typed directly by the user, and echoed in tool calls to configure
it) — confirmed via full git-history search and a working-tree grep that it was **never
committed to any repository** (saved only in the credentials file and the VM's gitignored
`.env`), but the transcript exposure itself can't be retroactively scrubbed. Recommended
rotating the key for a real guarantee; user deferred that ("will see that later"), opting to
clear the chat session instead — flagged honestly that clearing chat history is not something
that can be verified to fully purge platform-side retention, unlike rotation which gives an
actual guarantee. Revisit if the user wants to rotate later.

## Git History (this session)

PR #71 (ERP-106) → PR #72 (ERP-100/101/102) → PR #73 (ERP-105) → PR #74 (ERP-104) → PR #75
(`develop`→`main` promotion, deployed live) → PR #76 (ERP-107/108/109 ticket filings, docs only,
not yet promoted to `main` as of this entry).

## Blockers

- **ERP-106 is blocked purely on money settling** — OpenRouter payment clears 2026-09-28. No
  code/config work remains; just re-test once real balance shows.

## Next Steps

1. Once the OpenRouter payment clears: re-run the same live test (upload a real test PDF via
   `ops-admin@self-hosted-rag-platform.internal`, real generation call, timed) to confirm it
   actually works and is meaningfully faster than the ~75-250s Modal cold starts. Clean up
   whatever throwaway test document is left on that account afterward (check
   `GET /documents` as ops-admin first — a "banana split supreme" test document may or may not
   still be present depending on how the last test in this session landed).
2. Agreed priority for what's next after ERP-106 clears: **ERP-108 (rate limiting)** first
   (real cost-exposure gap), then **ERP-109 (output guardrail)**, then **ERP-107** (retrieval
   settings persistence — touches the generation hot path, deserves more care), then **ERP-103**
   last (pagination, explicitly low urgency).
3. PR #76 (ERP-107/108/109 ticket filings) has not yet been promoted to `main` — purely
   docs, no urgency, but note it's a step behind if checking `main` vs `develop` next session.
4. User may want to rotate the OpenRouter API key later, having deferred it this session.
