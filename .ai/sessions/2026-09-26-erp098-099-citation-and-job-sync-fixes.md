# Session — Citation Marker Bug and Stale Job Reconciliation Fixes

Date: 2026-09-26
Tickets Touched: ERP-098, ERP-099

## Decisions

Both bugs were found live, via manual sanity-testing of the 2026-09-26 `develop`→`main`
promotion (PR #65) after it was deployed and the VM was switched from tracking `develop` to
`main`. Neither bug is related to that promotion's own tickets (ERP-091/087/089/090/096) — both
are pre-existing defects the sanity pass happened to surface.

- **ERP-098** (citation marker mismatch): chose to preserve the LLM's original 1-indexed
  prompt-position marker all the way through to the frontend (a new `Citation.marker` field),
  rather than rewriting the answer text's `[n]` markers server-side to match a repacked array.
  Simpler and avoids text-rewriting fragility.
- **ERP-099** (stale in-progress job): confirmed via a regression test that the "server reports
  a job as failed" path was already handled correctly by existing code — the actual gap was
  purely the missing "server no longer lists this job at all" reconciliation direction.

## Implementation Summary

Both fixed via TDD in an isolated worktree (`erp-098-099-citation-job-sync-fixes`), each with a
failing test confirmed before any production code changed:

- `app/generation/schemas.py`: `Citation` gained `marker: int` (default `0` for pre-existing
  persisted citations).
- `app/generation/service.py`: `_cited_chunks` now returns `(marker, chunk)` pairs; `_citations_for`
  threads `marker` into each `Citation`.
- `frontend/src/lib/types.ts`/`markdownLite.tsx`: `Citation.marker` added; inline `[n]` marker
  resolution now matches on `citation.marker`, not array position.
- `frontend/src/pages/ChatPage.tsx`: footer citation list now renders `citation.marker` instead
  of `citationIndex + 1`.
- `frontend/src/pages/DocumentsPage.tsx`: `hydrateActiveJobsFromServer` now reconciles locally-
  seeded `pending`/`processing` jobs the server no longer reports as active (removes them,
  refreshes `serverDocuments`) instead of only ever adding server-known jobs.
- Two pre-existing ERP-091 "cold-start hint" tests had their mocks corrected to report the
  seeded job as genuinely still server-active (the ERP-099 fix correctly reconciled away their
  previous, unrealistic "processing locally, zero active jobs server-side" setup).

**Verified**: backend 558 passed (was 556), ruff/mypy clean. Frontend 90 passed (was 86), `tsc -b`
and `oxlint` clean.

## Blockers

None.

## Next Steps

- Push branch, open PR against `develop`, wait for explicit merge instruction.
- Once merged and promoted, live-verify both fixes against the real deployment (not yet done —
  no live re-verification performed this session, matching this project's practice of calling
  that out explicitly rather than assuming a passing test suite implies live correctness).
- ERP-097 (async sampling of live production traffic for LLM-judge scoring) is next up per the
  user, pending a separate discussion — the user said they have "something on my mind" for it,
  not yet raised.
