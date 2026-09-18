# Session — Web UI Live Review, Bugfix Rounds, and a Production VM Outage

Date: 2026-09-17
Tickets Touched: ERP-043, ERP-044, ERP-045, ERP-046, ERP-047

## Decisions

- Split live-review feedback into "fix now" (real bugs) vs. "log for later" (genuine new
  scope) rather than bundling everything into one pass — see ERP-043's bugfix PRs vs.
  ERP-044/045/046.
- Added a `Category` field (Bug / Improvement / Lapse) to the ticket framework
  (`.ai/tickets/README.md`) so a batch of tickets opened in one review session can be told apart
  at a glance and tracked together (`current-state.md`'s Next Planned Work).
- Root-caused a real production outage (VM became fully unresponsive, including SSH, after a PDF
  upload) to `docling`'s fallback parser exceeding the live VM's tiny memory budget. Researched
  current alternatives live (per `CLAUDE.md`'s research-before-recommending rule) rather than
  guessing: ruled out Render Starter as a fallback (it's 512MB RAM, less than the current VM —
  the previously-documented fallback plan was actually wrong once checked), ruled out Surya OCR
  (needs 4-8GB), and landed on **offloading `docling` to Google Cloud Run** rather than either
  swapping it for a lighter library stack or offloading to Modal (Modal is GPU-priced; `docling`
  needs no GPU, so Cloud Run's CPU/memory-only billing and genuinely recurring free tier — not a
  spend-down credit — fit better, and it's the same GCP account already in use for the VM).
- Added a standing architecture principle (`docs/architecture.md`): keep the always-on VM thin,
  offload anything CPU/memory-heavy to serverless compute by default. This is the second time
  this exact pattern has been needed (Ollama on Modal was the first); codifying it so it's not
  re-litigated from scratch next time.

## Implementation Summary

**ERP-043 bugfix rounds** (PRs #38, #39, both merged and deployed):
- Fixed: SPA-routing 404 on refresh (`frontend/vercel.json` rewrite), cross-user
  `localStorage` leakage (conversations/documents stores now scoped by user ID), no visibility
  into who's logged in (new `GET /auth/me` backend endpoint + header email display), citation
  formatting (page numbers + filename per citation), no document visibility in Chat, no
  loading/typing indicator.
- A second real bug found during that same testing: `DocumentsPage`/`ChatPage` could hang
  forever on "Loading..." if the new `/auth/me` fetch was ever slow or failed, since both pages
  fully blocked rendering on it. Fixed by decoding the user ID directly from the already-stored
  JWT client-side (`frontend/src/lib/jwt.ts`, no network round-trip, no signature verification
  needed since it's UI-only scoping) — pages no longer block on anything async for their core
  functionality.
- Both rounds redeployed live (backend via VM restart, frontend via `vercel --prod`) and
  re-verified with the user directly clicking through the real deployed app each time.

**Production VM outage** (not part of any PR — an incident, not a planned change):
- A user-uploaded PDF triggered `docling`'s quality-fallback path around 09:44 UTC; the VM
  became fully unresponsive (`curl`, `ping` ICMP-only working, SSH hanging) by ~09:49 UTC.
  Diagnosed via `gcloud compute instances describe` (VM showed `RUNNING` — misleading, the OS
  itself was wedged) and, after a `gcloud compute instances reset` brought it back, the retained
  previous-boot journal (`journalctl -u rag-platform -b -1`) showing the exact `docling` pipeline
  init sequence right before logs stopped.
- Live research (not assumed) confirmed `docling`'s own GitHub issues document a ~4GB baseline
  RAM requirement and active memory-leak reports — nowhere close to this VM's 958MB, explaining
  why this wasn't a "big file" problem (the user hadn't tested any large files yet) — it's a
  content-triggered problem (any table or scanned page bails into `docling` regardless of file
  size).
- No code changed yet for the actual fix — this session ends with the incident resolved
  (manual reset) and the fix fully scoped (ERP-047), not built.

## Blockers

None for the completed work (ERP-043's two bugfix rounds are live and verified). ERP-047 (the
actual `docling`-offload fix) is scoped but not started — next session's likely starting point,
and the most urgent of the four open tickets since it's a live reliability gap, not a
nice-to-have.

## Next Steps

- Build ERP-047: swap mitigation first (quick, immediate safety net), then the real fix
  (Cloud Run service for `docling`, `parse_quality()` calls out to it over HTTP, explicit
  cold-start UX handling). Should go through a proper brainstorming/design pass given it's a
  real architecture change (new deployable service, removed dependencies).
- ERP-044 (document-scoped retrieval), ERP-045 (answer feedback), and ERP-046 (retrieval
  relevance guardrail) remain backlog, un-started, each needing its own design pass before
  implementation.
