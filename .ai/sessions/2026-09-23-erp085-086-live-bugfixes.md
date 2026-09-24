# Session — ERP-085/ERP-086 Live Bugfixes

Date: 2026-09-23
Tickets Touched: ERP-085, ERP-086

## Decisions

- Built via `superpowers:subagent-driven-development`: 4 implementation tasks plus 1 doc-closeout
  task, each independently task-reviewed and Approved.
- A final whole-branch code review ran after all tasks landed, finding 2 Important issues plus
  several Minor ones. This session's fix wave addresses both Important findings and the
  cheap/low-risk Minor ones directly, rather than looping back through the full subagent process
  for small fixes.
- ERP-086's Cloud Run `--memory 8Gi` redeploy is documented as the next step but was not run —
  it's a live, billable infrastructure change and needs explicit user confirmation before
  executing, not something to do unprompted mid-fix-wave.

## Implementation Summary

**ERP-085 (mobile layout bugs) — both bugs fixed and closed out:**
- `frontend/src/components/AppShell.tsx`: header/nav given `flex-wrap` (plus `gap-x-4 gap-y-2`)
  so the nav row drops below the title on narrow viewports instead of overlapping/forcing
  horizontal page scroll.
- `frontend/src/components/SourcePanel.tsx`: `overflow-x-hidden` added to the panel,
  `break-words` added to the metadata line and markdown body so long tokens wrap instead of
  clipping, and the Copy/Close header row made `sticky top-0` so it stays reachable.
- Fix wave follow-up: the sticky header used `mb-3` (margin-bottom), which is transparent —
  content scrolling underneath it showed through the gap directly beneath the pinned header.
  Changed to `pb-3` (padding-bottom) so the header's background covers the full spacing. New
  regression test added in `SourcePanel.test.tsx` asserting the header's className contains
  `pb-3` and not `mb-3`.
- `.ai/tickets/ERP-085.md` updated with a transparency note: the fixes were verified via
  `vitest`/jsdom `className` assertions and a clean `tsc`/`oxlint`/`vite build`, but per the
  ticket's own original premise, jsdom cannot observe real layout/wrapping/scroll behavior —
  real-device re-confirmation has not been done post-fix. Status stays `Done` and acceptance
  criteria stay checked (the code fix itself is genuinely complete and task-reviewed); this is
  an honesty note, not a re-open.

**ERP-086 (docling service error handling) — half done this session:**
- `app/ingestion/cloud_run_client.py`: `call_docling_service` previously raised the friendly
  "too large or complex" message for *any* non-2xx response, including 4xx cases (bad auth
  token, wrong URL, other misconfiguration) — which would falsely tell a user their document was
  the problem when it was actually server-side. Narrowed to only use that wording when
  `exc.response.status_code >= 500` (the actual OOM-kill/infra-failure case it was designed for);
  other statuses now raise a neutral "the document parsing service couldn't process this file
  right now" message instead. TDD: added a failing test for the 4xx case first
  (`test_call_docling_service_raises_generic_message_on_4xx_response`), confirmed RED, then
  implemented and confirmed GREEN alongside the existing 503 test.
- The other half of ERP-086 — the Cloud Run `--memory 8Gi` redeploy to actually fix the
  underlying OOM-kill condition — is documented but **not yet run**. It's a live/billable
  infrastructure action and is pending explicit user confirmation.

## Blockers

None for the work completed. The Cloud Run redeploy is not blocked technically, just withheld
pending user go-ahead (see Next Steps).

## Next Steps

1. Real-device or real-browser re-confirmation of ERP-085's fixes (header wrap, SourcePanel
   overflow/wrap/sticky header) — the same kind of check that originally found these bugs, not
   yet repeated post-fix. See the verification-gap note in `.ai/tickets/ERP-085.md`.
2. Get explicit user confirmation, then run ERP-086's Cloud Run `--memory 8Gi` redeploy — this
   session only narrowed the error *message*; the underlying OOM-kill condition that produces
   5xx responses in the first place is still unaddressed until the redeploy happens.
