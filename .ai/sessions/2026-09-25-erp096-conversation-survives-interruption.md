# Session — ERP-096: live reproduction and full fix

Date: 2026-09-25
Tickets Touched: ERP-096 (Done)

## Decisions

- User supplied a screen recording (`rag_platform_refresh_tab_switch_issue.mp4`) and two HAR
  captures reproducing the bug live, resolving what code-only investigation in the prior session
  couldn't. Analysis method: `ffmpeg` frame extraction (a contact sheet first, then targeted
  full-resolution frames once the interesting timestamps were narrowed down) plus HAR
  request-type inspection (`_resourceType`/`rt` field — `document` means a real navigation,
  `fetch` means same-page JS).
- Found **two distinct mechanisms**, not one:
  - Refresh mid-stream: confirmed real page navigation (HAR shows `GET /chat` as `document`
    type, 304; a video frame shows the cursor on the browser's own reload button right before
    it). Root cause was already suspected from code reading alone (persistence only happens
    after the full answer assembles) — the live evidence confirmed it decisively.
  - Tab-switch: the *opposite* proof — zero `document`-type requests in that HAR, only
    same-page `fetch` calls, but `ChatPage`'s three mount-effects (`/documents`,
    `/conversations`, `/conversations/{id}`) fire together twice, 31 seconds apart. This
    is unambiguously a same-page React remount, not deferred repaint and not a page reload.
  - The remount's *trigger* was never found. Ruled out via direct evidence: dev-build/StrictMode
    double-effects (confirmed real production minified bundle), the unstable `key={index}`
    theory (doesn't explain new fetches), any visibility/lifecycle code anywhere in the app or
    its dependencies (grepped the actual deployed JS bundle for
    `visibilitychange`/`pageshow`/`freeze`/`resume`/`bfcache` — zero matches). Decided not to
    keep chasing this given the fix below addresses the user-visible impact regardless of cause.
- User's explicit requirement for the fix: "user should not feel that the conversation is lost."
  This shaped the design — fixing only the tab-switch remount's trigger wouldn't have helped the
  refresh case at all, so the fix targets the shared root cause (fragile persistence timing) both
  scenarios expose, not either symptom individually.

## Implementation Summary

- `app/generation/service.py`: `generate_stream`'s stateful branch now persists the user's
  question immediately (before generation starts) and delegates all rewrite/retrieval/
  generation/persistence work to a new `_run_stateful_generation`, run in a background thread
  via `contextvars.copy_context().run(...)` (propagates OTel trace context, so ERP-028/089's
  `retrieval.fuse`/`embedding.generate`/etc. spans still nest correctly instead of becoming
  orphaned root traces). Relays events to the live SSE client through a `queue.Queue`; a client
  disconnect only stops the relay, never the underlying work. On failure, persists a new
  `FAILURE_NOTICE` assistant message instead of leaving the question dangling unanswered.
- `frontend/src/pages/ChatPage.tsx`: new `rag-pending-answer:<id>` localStorage marker, set when
  a live stream starts and cleared only on explicit `done`/`error` (deliberately never in
  `finally`, since surviving a client-side network hiccup is the whole point). `loadConversationHistory`
  checks on every mount/remount for a trailing unanswered user message + that marker, and if
  found, shows the same pending-bubble UI ERP-091 already built and polls
  `GET /conversations/{id}` (2s interval, 2-minute timeout with a clear give-up message) until
  the reply lands. A `conversationIdRef` guards against a slow poll overwriting the screen if the
  user's switched conversations meanwhile.
- Tests: 3 new/updated in `tests/generation/test_service.py`, 2 new in `ChatPage.test.tsx`.
- Merged to `develop` via PR #64. Full backend suite 556 passed, frontend 86 passed throughout.

## Blockers / Known Gaps

- Not live-re-verified against the real deployment — would need a genuine refresh and tab-switch
  repro against production (same method the user already used) to confirm the fix holds up there
  too, not just in tests.
- The tab-switch remount's root trigger is still unknown. Doesn't block the fix (which is
  trigger-agnostic) but would be worth understanding eventually, e.g. via live React DevTools
  "why did this render" profiling — something this environment can't do.

## Next Steps

- If picked up again: live-verify ERP-096's fix on the real deployment.
- Separately (raised by the user, explicitly not urgent): no real feedback-collection process
  exists beyond ad hoc relay into a session — see `current-state.md`'s new entry on this for the
  two options already discussed, so they don't need re-deriving.
- ERP-097 (async sampling of real production traffic for evaluation) is still unbuilt — the
  evaluation dashboard (ERP-087) only reflects the golden dataset, which the user flagged this
  session as a real limitation.
