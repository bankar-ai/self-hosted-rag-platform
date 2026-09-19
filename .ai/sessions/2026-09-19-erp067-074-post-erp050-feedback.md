# Session — Post-ERP-050 Live Feedback Batch (ERP-067 – ERP-074)

Date: 2026-09-19
Tickets Touched: ERP-067, ERP-068, ERP-069, ERP-070, ERP-071, ERP-072, ERP-073, ERP-074

## Decisions

- Investigated all five raw observations from live ERP-050 testing before scoping anything:
  confirmed the source panel's raw-markdown display was a real bug (not just a style nit),
  investigated document-deletion completeness (found it was already correct — no fix needed),
  investigated the upload-failure/retry story (found two real gaps: dismissed-job disk leak,
  and silent upload-transfer failures), and traced the Documents page's invisible button text
  to a genuine root cause (Tailwind cascade-order conflict in `Button`'s hardcoded default
  colors) rather than patching individual buttons.
- The hallucinated date was confirmed genuine, not a UI-truncation artifact, by reading the
  user-supplied full source PDF and searching it for the claimed date — it doesn't appear
  anywhere in the document.
- `react-markdown` + `rehype-raw` + `rehype-sanitize` chosen over `marked` + `dompurify` for
  `SourcePanel`'s markdown rendering, based on current guidance that `react-markdown` avoids
  `dangerouslySetInnerHTML` entirely (renders to React elements directly) — the safer default
  for content whose origin (parsed PDFs) isn't fully trusted.
- Scoped as 8 separate tickets under one shared spec/plan, mirroring the ERP-045 batch's
  pattern — distinct concerns, one coherent implementation pass.
- Executed via subagent-driven development, same as ERP-050: 7 tasks, each independently
  reviewed, plus a final whole-branch review on the most capable available model.

## Implementation Summary

- `frontend/src/components/ui/button.tsx` — `variant` prop (`primary`/`outline`/`danger`).
- `frontend/src/components/SourcePanel.tsx` — real markdown/HTML rendering via `react-markdown`;
  copy button in its header.
- `frontend/src/components/CopyButton.tsx` (new) — shared copy-to-clipboard component, wired
  into `SourcePanel` and twice into `ChatPage.tsx` (per-message, per-conversation).
- `app/generation/prompt.py` — `SYSTEM_PROMPT` gained an explicit anti-fabrication clause.
- `app/ingestion/jobs.py`/`router.py` — `delete_job` / `DELETE /ingestion/jobs/{job_id}`.
- `frontend/src/pages/DocumentsPage.tsx` — staged upload, bulk delete + confirmation, dismiss
  wiring to the new backend endpoint, upload-transfer failure UI with retry and dismiss; first
  test file for this page (`DocumentsPage.test.tsx`).

## Process Notes

- **Task 5 fix round**: the first attempt at `delete_job` deviated significantly from the
  plan — added an unauthorized 15-attempt retry loop with cumulative `time.sleep()` (up to
  ~1.5s) and modified the existing, previously-stable `run_ingestion_job` function, chasing a
  Windows-specific PyMuPDF file-locking issue in the implementer's own test environment. None
  of this fixed the test, by the implementer's own admission. Task review caught it, ruled it
  out as unauthorized scope creep plus unacceptable request-path latency, and the fix round
  reverted both back to the plan's simple code — the actual test flakiness was fixed at its
  root by using this codebase's own established `monkeypatch.setattr(jobs_module, "ingest_pdf", ...)`
  failure-injection pattern (already used elsewhere in the same test file) instead of exercising
  real PyMuPDF parsing.
- **Task 6's implementer caught a real bug in this session's own plan**: the plan's test setup
  used a literal placeholder token (`setTokens({accessToken: "a", ...})`) that isn't a decodable
  JWT — `AuthContext`'s `userId` derivation requires a real `.`-delimited token, so the page
  would have rendered `null` forever regardless of implementation correctness. Fixed with a
  `fakeToken()` helper matching an existing pattern in `jwt.test.ts`. Independently confirmed by
  the task reviewer against the real `AuthContext.tsx` source before accepting the deviation.
- **Final whole-branch review found a genuine Critical, plan-mandated bug**: the plan's own
  Task 6 code specified `handleRetry` calling `await dismissInProgress(jobId)` — but
  `dismissInProgress` had just gained (via this same batch's Task 5) a call to the new backend
  `DELETE /ingestion/jobs/{job_id}` endpoint, which unlinks the job's temp file. `retry_job`
  deliberately reuses that same file so a retry doesn't need a re-upload — so retrying could
  race-delete the file the retry itself was about to read, an unrecoverable data-loss bug in
  the pre-existing ERP-053 retry feature. This was a defect in the plan text itself, faithfully
  implemented, not implementer drift. Fixed by splitting `dismissInProgress` into a local-only
  cleanup (used by retry) and the full backend-deleting version (used only by an explicit
  "Dismiss" click) — ruled and fixed in one pass along with two related Important findings
  (bulk delete silently "succeeding" in the UI on a failed request; a network error leaving
  delete rows stuck disabled forever).
- Commit-attribution drift (implementer subagents defaulting to their own model identity in the
  `Co-Authored-By` trailer instead of the session's mandated one) recurred from the ERP-050
  session — fixed the same way, one `git filter-branch --msg-filter` pass before the final
  review, on the still-unpushed branch.
- The plan document itself was written but never committed until noticed just before the final
  review (a `git status` check caught it) — committed before proceeding.

## Verification

- Backend: `ruff check .` / `mypy app/` clean; `pytest -q` 516 passed (was 508).
- Frontend: `npm run build` / `npm run lint` clean; `npm test` 55 passed (was 36). New coverage:
  `Button` variants, `SourcePanel` markdown rendering + sanitization + copy, `CopyButton`'s lazy
  evaluation, and `DocumentsPage.test.tsx` (first test file for this page) covering staged
  upload, per-file removal, transfer-failure retry/dismiss, delete confirmation (accept/decline),
  bulk delete (per-document URL verification, not just a call count), and the retry-vs-dismiss
  regression guard for the Critical finding above.
- PR #50 → `develop`, CI `test` check passed, merged.
- Deployed: backend via `git pull` + `systemctl restart` (no migration); frontend via
  `vercel --prod --scope bankar-ai`.
- **Live-verified against production**: reproduced the exact hallucination conversation from the
  original bug report end-to-end (re-ingested the same ladder-fatality report text, asked the
  same two-turn conversation) — confirmed the model now says "The context does not specify the
  date of the death" instead of asserting a fabricated date. Separately, live-verified the
  disk-leak fix via SSH: uploaded a file that fails ingestion, confirmed its temp file existed on
  the VM's disk, dismissed the job via the new endpoint, confirmed via a second SSH check that
  both the file and its parent directory were actually gone. Test user deleted afterward via the
  admin API.
- **Not verified**: the frontend-only UI changes (staged upload flow, bulk-delete checkboxes,
  copy buttons, source-panel markdown rendering) were not visually screenshot-verified in a live
  browser — no browser tool is available in this environment. Verified instead via the
  component-level test coverage above plus careful diff-level review at each task gate,
  consistent with how ERP-050's `ChatPage.tsx` wiring was handled.

## Blockers

None.

## Next Steps

- Recommended follow-up (not a blocker, not yet ticketed): lazy-load `SourcePanel` via
  `React.lazy()` to keep the new markdown-rendering dependency tree (~300KB) out of the main
  bundle for pages that can never render it (Login, Documents).
- Minor deferred items (see individual ticket Resolution notes): prompt wording ("verbatim
  present") worth watching during ordinary use for over-constraining legitimate derived/
  paraphrased answers; a confirmation-declined test could more directly assert zero delete
  calls; no indeterminate state on the Documents page's select-all checkbox.
