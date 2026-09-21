# Session — ERP-083 deployment, live-verification, and two post-deploy bug fixes

Date: 2026-09-20
Tickets Touched: ERP-076, ERP-080, ERP-083, ERP-084 (new)

## Decisions

- **Evaluation gate schedule: manual-only for now**, not daily. User's call: paying for a live-LLM
  evaluation run every day regardless of whether anything changed wastes Modal spend for no new
  signal. `workflow_dispatch` only; a schedule (weekly was the leading candidate) can be added
  back once there's a sense of actual needed frequency. (PR #52, before this session's work
  properly started, but the decision carried through everything below.)
- **CI-config-only fixes target `main` directly**, skipping the `develop` → `main` round-trip —
  applied twice this session (PR #54 for the evaluation workflow's model config, PR #55 for the
  two frontend citation/markdown bugs). Reasoning: no application code involved, and in both
  cases the fix was needed live immediately, not worth the delay of a second promotion cycle.
- **Cloud Run and the main app are genuinely independent deploys** — ERP-076's own ticket Notes
  already said this, and it still got missed on the first deploy pass (see Blockers). Worth
  treating as a standing checklist item, not just documentation, for any future ticket that
  touches both deployables.

## Implementation Summary

This session picked up where the ERP-075-083 branch work (previous session,
`2026-09-19-erp075-083-live-feedback-followups.md`) left off — PR #51 was already open and
merged into `develop` at the start of this session. From there:

1. **Vercel Root Directory bug found and fixed** (not a code issue): PR #51's Vercel preview
   deployment failed with `vite: command not found` — turned out to be the Vercel project's
   Root Directory setting pointing at the repo root instead of `frontend/`, unrelated to any
   code in this batch. User fixed it in the Vercel dashboard; redeploy succeeded.
2. **PR #52**: dropped the evaluation-gate workflow's daily `schedule:` trigger per the user's
   cost-conscious call above, manual-only (`workflow_dispatch`) for now.
3. **`develop` → `main` promotion (PR #53)**: brought `main` up to date with everything merged
   to `develop` since PR #35 (2026-09-16) — ERP-044, ERP-050, ERP-067-074, and this session's
   ERP-075-083 batch. All-green CI, merged.
4. **ERP-083 deployment** (the three manual steps its own ticket had flagged):
   - Payment method attached to Modal by the user.
   - `deploy/modal_ollama_ci.py` deployed — first attempt failed (`FileNotFoundError`, run from
     a checkout that didn't have the file since the PR wasn't merged yet); re-run from the
     worktree succeeded. Hit a Windows-specific `charmap codec` crash from the Modal CLI's own
     Unicode progress characters, worked around with `PYTHONUTF8=1 PYTHONIOENCODING=utf-8`.
     Endpoint verified reachable (`200`) before wiring in.
   - Endpoint URL added as the `CI_MODAL_OLLAMA_URL` GitHub secret.
5. **First evaluation-gate run failed**: retrieval-quality passed cleanly (exact match to the
   ERP-029/041 baseline), but generation-quality crashed — `GenerationSettings.model` defaults
   to `"qwen3"`, not one of the two models baked into the CI Modal image. Fixed with
   `GENERATION_MODEL: gemma3:4b` in the workflow env (PR #54, targeted `main` directly).
   **Second run passed both gates.**
6. **VM backend deploy**: `git pull` (fast-forwarded cleanly), `alembic upgrade head` (both new
   migrations applied — citations column, parsing_confidence column), `systemctl restart
   rag-platform`. Hit the documented `.env`-has-an-unquoted-`&`-that-breaks-bash-`source`
   gotcha when the plain `uv run alembic upgrade head` connected to `localhost:5432` instead of
   the real Neon DB; worked around with a small Python script (loads `.env` manually into
   `os.environ`, invokes `uv run alembic` as a subprocess with that env) delivered via the
   documented base64-over-SSH pattern (avoids the 4-layer PowerShell→gcloud.cmd→SSH→bash quoting
   problem). Verified live: `GET /docs` → `200`.
7. **Live-verification surfaced two real bugs**, both fixed same-session:
   - **ERP-076's Cloud Run redeploy was missed.** The user retried two real scanned PDFs that
     had failed with `TypeError: list indices must be integers or slices, not str`. Root cause:
     the main app's `call_docling_service` (already deployed) expected the Cloud Run
     `docling-service`'s new `{"pages":..., "confidence":...}` response shape, but the Cloud Run
     service itself was still serving its *old* code (a bare list) — ERP-076's own ticket had
     flagged this exact redeploy requirement in its Notes, and it still got missed on the first
     pass. Fixed by actually running `gcloud run deploy self-hosted-rag-platform-docling
     --source deploy/cloud_run_docling ...` (no code change, the fix already existed in the
     repo — new revision `-00003`, confirmed serving). User re-verified by retrying the same
     uploads.
   - **ERP-084 (new ticket): bundled citation markers, section_path markdown.** User reported
     `[1, 3, 4]`-style citation markers weren't clickable, and a document's section-path
     breadcrumb showed literal `**`/`<mark>` syntax. Diagnosed both as pre-existing gaps, not
     regressions: the frontend citation regex never matched a bundled marker (only single `[n]`,
     even though the backend has tolerated bundling for citation *extraction* since ERP-065);
     `section_path` was always plain-text-rendered, never markdown-rendered (unlike the chunk
     body, which correctly does via ERP-067's `ReactMarkdown` fix). Fixed both, merged directly
     to `main` (PR #55), verified live by the user.

## Blockers

None remaining. Every open item from the previous session's handoff is now closed:
- ~~ERP-083's 3 manual steps~~ — done
- ~~Manual evaluation-gate test run~~ — done, passing
- ~~VM deploy~~ — done, verified live
- ~~Live-verify ERP-076/ERP-080~~ — done (found and fixed the Cloud Run gap along the way)
- Manual browser check of ERP-078 (responsive layout) / ERP-079 (keyboard trap) — **still not
  done this session either**; user explicitly deprioritized it ("I do not care about the 078,
  079 for now"). Worth a follow-up pass eventually, no urgency.

## Next Steps

- No open work from this batch. `main` and `develop` are back in sync as of PR #55's merge.
- Whenever convenient: the deprioritized manual browser check of ERP-078/ERP-079.
- Longer-term, per ERP-076's Resolution note: build some kind of check (even just a personal
  habit/checklist, not necessarily code) for "does this ticket touch a second deployable" before
  calling a deploy done — this is now the second time in this project's history a Cloud-Run-vs-
  main-app deploy mismatch has caused a live bug (first was ERP-047 itself surfacing the need
  for the split; this is the first *regression* from forgetting to redeploy both sides).
