# Session — Feedback, Markdown Rendering, Duplicate Names, Auto-Scroll, Citation Parsing

Date: 2026-09-18
Tickets Touched: ERP-045, ERP-062, ERP-063, ERP-064, ERP-065

## Decisions

- Given a complete list of 7 pending items (ERP-044, ERP-045, ERP-050, plus 4 new observations),
  deliberately split into two tiers: 5 same-session-sized items (this batch) vs. 2 bigger,
  separately-scoped efforts (ERP-044's real retrieval-pipeline design, ERP-050's open-ended
  visual work) — folding all 7 into one pass would have blown up scope and risk for no reason.
- ERP-045 (feedback) surfaced a real prerequisite gap while scoping it: nothing in the API
  exposed a message's ID at all, so there was nothing for the frontend to attach a rating to.
  Rather than route around this, fixed it properly — `GenerationResponse` and the streaming
  `done` event both gained `assistant_message_id`, and `Message` gained `id`. This was treated
  as part of ERP-045's own scope, not a separate ticket, since it has no purpose on its own.
- ERP-062 (duplicate names) was deliberately scoped to the *rename* action only, not
  auto-derived titles — two unrelated conversations both starting with "hi" is normal usage
  (visible in the reported screenshot itself), and enforcing uniqueness there would fight
  existing behavior rather than fix a bug.
- ERP-063 (markdown rendering) deliberately did not add a markdown/remark dependency. The model
  is only ever instructed to use a narrow subset (bold, bullet/numbered lists) via its own
  system prompt, so a ~70-line hand-rolled renderer covers exactly that without bundle growth
  or a new dependency requiring approval under this repo's dependency policy.
- The citation-parsing bug (ERP-065) was root-caused precisely from the user's own screenshot
  before writing any code: `[1, 2, 5]` (comma-separated, one bracket) versus the regex's
  single-digit-only pattern, explaining exactly why no citation list rendered at all for that
  message.

## Implementation Summary

**Backend** (`app/generation/`):
- `service.py`: `_CITATION_MARKER_RE` now matches `\[(\d+(?:\s*,\s*\d+)*)\]`, comma-split in
  `_cited_chunks`. New `ConversationTitleConflictError`; `rename_conversation` checks
  `title_exists_for_owner` before renaming. New `set_feedback`/`clear_feedback`. `generate()`/
  `generate_stream()` capture and return the persisted assistant message's ID.
- `repository.py`: `title_exists_for_owner` (case-insensitive, self-exclusion-aware). New
  `MessageFeedbackRecord`-backed `set_message_feedback`/`clear_message_feedback`/
  `get_feedback_for_messages` (batched, ownership-checked via a join through the owning
  conversation -- never trusts a bare message ID).
- `models.py`: new `MessageFeedbackRecord` (one row per `message_id`, upserted in place).
  Migration `2da7a6112cf0`, empty-diff-confirmed.
- `schemas.py`: `Citation`/`GenerationResponse` gained `assistant_message_id`; `Message` gained
  `id`/`feedback`; new `RenameConversationRequest` conflict path; new
  `SetMessageFeedbackRequest`.
- `router.py`: `PATCH /conversations/{id}` now 409s on a title conflict. New
  `PUT`/`DELETE /conversations/messages/{message_id}/feedback`.
- `prompt.py`: `SYSTEM_PROMPT` gained explicit list-formatting guidance, a preference for
  `[1][2]` over `[1, 2]` (the parser handles both regardless), and a stronger
  never-guess-or-assume line.

**Frontend**:
- New `frontend/src/lib/markdownLite.tsx` (`renderMarkdownLite`) -- bold, bullet/numbered
  lists, paragraphs. `frontend/src/lib/markdownLite.test.tsx` (5 tests, via
  `@testing-library/react`, already a dependency).
- `ChatPage.tsx`: renders messages through `renderMarkdownLite` instead of raw `<p>`; a
  bottom-anchor ref + `useEffect` on `messages` auto-scrolls (ERP-064); `renameConversation`
  surfaces a 409 as a clear `window.alert`; thumbs up/down buttons per assistant message once
  its ID is known (from the streaming `done` event or history reload), toggle-to-clear via
  `DELETE` when clicking the already-active rating.
- `types.ts`: `ConversationMessage` gained `id`/`feedback`.

## Verification

Backend: ruff/mypy clean, full suite 461 → 485 tests passing. Frontend: `tsc --noEmit`/`oxlint`
clean, 24 → 29 tests passing.

Live end-to-end against the real local stack (real Postgres/Redis/Ollama, not fakes), before
deploying:
- Ingested a real document, asked a question spanning two facts in the same chunk — confirmed
  `assistant_message_id` present in the response.
- Feedback: set "up", confirmed in a fresh history fetch; switched to "down", confirmed;
  cleared, confirmed `null` again -- the full round trip through persistence and reload, not
  just the initial set.
- Duplicate names: renamed one conversation to "Shared Name", attempted to rename a second to
  "shared name" (different case) -- correctly `409`.
- Greeting fast-path re-confirmed still working after the `SYSTEM_PROMPT` edit, with
  `assistant_message_id: null` for the stateless case as expected.

Markdown rendering and auto-scroll are pure frontend/DOM behaviors verified via the automated
`markdownLite` unit tests and manual dev-server boot (no browser tool available this session for
a visual click-through -- flagged, not silently assumed).

## Blockers

None -- unlike the previous two sessions, no permission-classifier-blocked actions were needed
this time (no live-VM mutation or PR merge attempted mid-session).

## Next Steps

- Push, PR, merge, deploy (backend `git pull` + migrate + restart; frontend should now
  auto-deploy via the Git integration connected last session -- first real test of that).
- Live-verify this batch against the actual deployment once shipped.
- ERP-044 (document-scoped retrieval) and ERP-050 (visual redesign) remain `Backlog`,
  deliberately deferred as their own future efforts.
