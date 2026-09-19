# Session — ERP-075 through ERP-083: copy/a11y/responsive/confidence/citations/CI-eval follow-ups

Date: 2026-09-19
Tickets Touched: ERP-075, ERP-076, ERP-077, ERP-078, ERP-079, ERP-080, ERP-081, ERP-083

## Decisions

- **ERP-076 parsing-confidence source**: use Docling's own document-level `ConfidenceReport`
  (`mean_grade`: poor/fair/good/excellent/unspecified) rather than a hand-rolled heuristic,
  researched live (WebSearch + WebFetch against Docling's docs/source) before writing code.
  Fast-path-only documents (no Docling call at all) default to a fixed `"high"` label rather
  than paying for an extra Docling call purely to score them.
- **ERP-083 CI-evaluation backend**: reuse the existing, already-proven `deploy/modal_ollama.py`
  pattern via a **second, separate** Modal app (`modal_ollama_ci.py`) rather than the production
  endpoint or new GCP infrastructure — researched live (WebSearch) and cross-checked against
  this project's own `gcp-deployment-tracker.md`, which records a real 2026-09-17 incident where
  the production Modal endpoint was disabled after crossing its $1-free-credit threshold. A
  shared endpoint would let CI usage re-trigger that same failure and take production down too.
  Scheduled (daily + `workflow_dispatch`), not a per-PR gate, to avoid GPU cold-start latency on
  every PR.
- **ERP-078 responsive layout**: below `md`, the sidebar becomes a slide-in overlay (backdrop,
  hamburger toggle, auto-closes on navigation) and the source panel becomes a full-screen
  overlay, rather than any attempt to keep three static columns on a narrow viewport.
- Scope explicitly excluded ERP-082 (OIDC self-service identity linking) and DOCX/PPTX ingestion,
  per user instruction — not touched this session.

## Implementation Summary

Executed via a dedicated worktree (`erp075-083-followups`, based on `develop`, not `main` —
`main` lags `develop` by several tickets in this repo), one commit per ticket:

- **ERP-075**: per-turn (Q+A) copy button in the chat view; per-row copy on the sidebar's
  conversation list (fetches that conversation's transcript on demand, since the sidebar only
  ever holds an id/title). `CopyButton.getText` now accepts `string | Promise<string>`.
- **ERP-081**: `SourcePanel` (which pulls in `react-markdown`/`rehype-raw`/`rehype-sanitize`,
  ERP-067) is now `React.lazy` + `Suspense`-loaded. Main bundle: 581KB → 287KB minified.
- **ERP-077**: a `[n]` citation marker inside `**bold**` text is now clickable — `renderInline`'s
  bold branch previously rendered bold text as a raw string, never routing it through the
  citation-rendering path at all.
- **ERP-079**: `SourcePanel` is a real dialog now — `role="dialog"`/`aria-modal`/`aria-label`,
  focus moves in on open, Escape closes it, Tab/Shift+Tab are trapped inside it, and closing it
  returns focus to whichever citation trigger opened it (new `sourcePanelTriggerRef` in
  `ChatPage`).
- **ERP-078**: sidebar and source panel both become overlays below `md`; hamburger toggle added
  to the chat pane header.
- **ERP-080**: `conversation_messages` gained a nullable `citations` JSONB column (Alembic
  migration `0e0c25ec1392`, empty-diff and downgrade/upgrade round-trip both verified against
  the real dev-stack Postgres). `generate()`/`generate_stream()` now persist citations alongside
  the assistant turn; `GET /conversations/{id}` returns them; the frontend maps them from
  reloaded history the same way it already does for a live streamed response.
- **ERP-076**: the Cloud Run `docling-service`'s `/parse` response shape changed from a bare
  page list to `{"pages": [...], "confidence": "..."}`. `call_docling_service`/`parse_quality`/
  `parse_pdf`/`ingest_pdf` all thread the confidence value through; new `documents.
  parsing_confidence` column (Alembic migration `4c6edf78ba5c`, backfilled `'high'` for existing
  rows); exposed via the Documents API; rendered as a small colored badge (new
  `ConfidenceBadge.tsx`) next to each filename in both the Documents page and the Chat sidebar's
  document list.
- **ERP-083**: code-complete, not deployed (see Blockers). New `deploy/modal_ollama_ci.py`
  (isolated CI-only Modal app); `app.evaluation.run`/`generation_run` both gained `--fail-under-*`
  flags (new `check_thresholds` helper in each, unit-tested, no behavior change when omitted);
  new `.github/workflows/evaluation-gate.yml` (daily cron + `workflow_dispatch`) wires them
  together against `secrets.CI_MODAL_OLLAMA_URL`, using `--judge ollama` (not `ragas` — ERP-030
  found it hangs against local Ollama) with thresholds set just below the ERP-029/041 verified
  baseline for retrieval, and a conservative initial floor for generation (no run history yet).

Verified per ticket, and again at the end of the batch: `ruff`/`mypy` clean throughout; backend
suite grew 516 → 528 tests, all passing (real Postgres/Redis via the main checkout's already-
running dev-stack — a duplicate worktree-local stack was attempted first and torn down after a
port conflict); frontend `tsc`/`vite build`/`oxlint` clean throughout, tests grew 29 → 60 passing
(the 29 baseline was itself a false start — the worktree initially branched from stale `origin/
main` and was reset to `origin/develop`, whose actual baseline was 55 frontend / 516 backend).

## Blockers

**ERP-083's actual deployment** — three steps deliberately left undone this session, each a
live/billable/credential-touching action outside what an agent session does unprompted:
1. `uv run modal deploy deploy/modal_ollama_ci.py` (creates real paid cloud infrastructure).
2. Attaching a payment method to the Modal account.
3. Adding the deployed endpoint's URL as the `CI_MODAL_OLLAMA_URL` GitHub Actions repo secret.

Exact steps documented in `D:\github-projects\gcp-deployment-tracker.md`'s new "CI evaluation
gate" section. Until done, the workflow exists but fails fast with a clear error instead of
running (it does not silently no-op).

**No live browser verification** for the frontend changes this session (ERP-075/077/078/079/081)
— no browser-automation tool was available in this environment. Verified via `tsc`, `oxlint`,
`vitest`, and `vite build` only; a manual click-through in a real browser (especially ERP-078's
responsive breakpoints and ERP-079's keyboard trap) is recommended before/soon after deploying.

## Next Steps

- Complete ERP-083's three manual steps, then confirm the scheduled workflow actually runs green
  (or correctly fails) at least once.
- Deploy this batch: `git pull` + `systemctl restart` on the VM (no new migration-sensitive
  behavior beyond the two Alembic migrations, which need `alembic upgrade head` first — same
  pattern as every prior deploy) + `vercel --prod --scope bankar-ai` for the frontend.
- Live-verify against production once deployed, per this project's established practice: at
  minimum, confirm the citations-survive-reload fix (ERP-080) and the parsing-confidence badge
  (ERP-076) against a real scanned document.
- A manual click-through of the responsive layout (ERP-078) and keyboard navigation (ERP-079) in
  a real browser, to cover what this session's automated checks couldn't.
