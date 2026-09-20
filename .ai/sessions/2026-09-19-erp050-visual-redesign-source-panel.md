# Session — Visual/UX Redesign: Source Panel + Three-Pane Layout (ERP-050)

Date: 2026-09-19
Tickets Touched: ERP-050

## Decisions

- Brainstormed from the ticket's own prior research (NotebookLM/Perplexity-style citation
  traceability) confirmed live with the user: the real gap was "answers can't be traced back to
  source," not raw color/typography.
- **No PDF storage or viewer** — ingestion never persists original PDF bytes; the source panel
  shows the already-durably-stored extracted chunk text instead. Zero new infrastructure.
- **Cited chunk only**, no sibling/section-expansion context in the panel — deliberately
  simpler for v1. Confirmed during brainstorming that sibling-chunk lookup is scoped
  per-document regardless of total document count, so this isn't a scaling concern if revisited.
- **Chunk text fetched on demand** via a new endpoint, not embedded in `Citation` — keeps every
  streamed answer lean regardless of citation count.
- **Both citation triggers** (inline `[n]` marker and the citation list) open the same panel —
  built as one feature, not staged separately.
- **Three-pane layout, not a NotebookLM "Studio" clone** — confirmed layout restructuring has
  zero infra cost (same API calls, different arrangement), and explicitly excluded a
  generated-artifacts capability nobody asked for.
- Executed via **subagent-driven development**: 8 tasks, each independently implemented and
  task-reviewed by a fresh subagent, plus a final whole-branch review on the most capable
  available model.

## Implementation Summary

Backend: `app/ingestion/repository.py` gained `get_chunk_by_document_and_owner`;
`app/ingestion/schemas.py` gained `ChunkDetailResponse`; `app/ingestion/service.py` gained
`get_chunk_detail`; `app/ingestion/router.py` gained `GET /documents/{document_id}/chunks/{chunk_id}`
(owner-scoped, undifferentiated 404).

Frontend: `frontend/src/components/SourcePanel.tsx` (new) — fetches and shows a citation's
chunk text on demand across 4 states (loading/loaded/not-found/error+retry), with
filename/page/section/score metadata rendered unconditionally (not gated on fetch success, per
a final-review fix). `frontend/src/lib/markdownLite.tsx` — `renderMarkdownLite` gained optional
`citations`/`onCitationClick` parameters, turning `[n]` markers into clickable buttons.
`frontend/src/components/Sidebar.tsx` (new) — pure extraction of `ChatPage.tsx`'s existing
sidebar JSX, no behavior change. `frontend/src/pages/ChatPage.tsx` — restructured into a
three-column layout, citation list and inline markers both wired to open `SourcePanel`.
`frontend/src/index.css` — a small Tailwind v4 `@theme` block (`--color-brand`,
`--color-brand-dark`), applied to the shared `Button`, chat bubbles, and sidebar active state.

## Process Notes

- Local dev-stack Postgres/Redis had been stopped earlier in the session (a prior, unrelated
  request to reduce idle resource use) — this broke Task 1's implementer's ability to get real
  GREEN test evidence. Caught by the controller before proceeding to task review; containers
  restarted, tests re-verified for real. Worth remembering: starting a subagent-driven-dev run
  right after stopping local infra is a foot-gun.
- All 8 implementer subagent commits initially carried their own model's identity in the
  `Co-Authored-By` trailer instead of the session's mandated `Claude Sonnet 5`, despite every
  dispatch specifying it verbatim. Fixed in one batch via `git filter-branch --msg-filter`
  before the final review (safe — branch was never pushed at that point).
- The final whole-branch review (dispatched on Opus) found one genuine pre-merge bug: a
  stale-response race in `SourcePanel` (switching citations before an earlier fetch resolves
  could silently show the wrong chunk's text under the new citation's header) — fixed with a
  `cancelled`-flag guard in the effect, verified by a scoped re-review. It also correctly
  affirmed two of the controller's own prior rulings (an extra `<span>` nesting introduced by
  the citation-marker parser, and skipping a dedicated `ChatPage.tsx` test harness) after
  independently checking for concrete downstream consequences and finding none.
- Vercel's CLI required an explicit `--scope bankar-ai` this session — a bare `vercel --prod`
  failed with "Not authorized" despite `vercel whoami` succeeding and the local `.vercel/project.json`
  link looking correct. Worth remembering for the next manual deploy.

## Verification

- Backend: `ruff check .` / `mypy app/` clean; `pytest -q` 508 passed (was 502). New tests cover
  the chunk-lookup function and endpoint at both layers (happy path, unknown chunk, cross-owner).
- Frontend: `npm run build` / `npm run lint` clean; `npm test` 36 passed (was 29). New tests
  cover `SourcePanel`'s four states (including metadata visibility on error/not-found) and
  `markdownLite`'s citation-marker parsing.
- PR #49 → `develop`, CI `test` check passed, merged.
- Deployed: backend via `git pull` + `systemctl restart` (no migration); frontend via
  `vercel --prod --scope bankar-ai`.
- **Live-verified against production**: uploaded a real document, ran a real generation query,
  confirmed its returned citation resolves correctly through the new chunk-detail endpoint
  (exact text match); confirmed an unknown chunk and a cross-owner request both 404; deleted the
  source document and confirmed its chunk endpoint now 404s too (the panel's "no longer
  available" trigger). Two throwaway test users deleted via the admin API afterward.
- **Not verified**: the actual click-to-open-panel interaction in a live browser — no browser
  tool is available in this environment. Verified instead via: the underlying components'
  thorough unit tests (`SourcePanel`, `markdownLite`), a diff-level wiring review by the final
  reviewer tracing every prop/handler/ordering requirement to a specific line, and the
  API-level end-to-end proof above that the data flowing into those components is correct.

## Blockers

None.

## Next Steps

Deferred follow-ups, not blockers (see `.ai/tickets/ERP-050.md`'s Resolution for full detail):
citation markers inside bold text aren't clickable; no responsive layout for narrow viewports;
no keyboard/focus/aria affordances on the source panel; a conversation restored from history has
no clickable citations at all (pre-existing gap, made more costly by this feature). None of
these were requested as part of this ticket's scope — worth their own ticket if picked up later.
