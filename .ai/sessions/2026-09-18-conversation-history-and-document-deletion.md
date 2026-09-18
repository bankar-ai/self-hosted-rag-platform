# Session — Conversation History Reload & Document Deletion (ERP-048/ERP-049)

Date: 2026-09-18
Tickets Touched: ERP-043, ERP-048, ERP-049, ERP-050 (opened, not implemented)

## Decisions

- Closed ERP-043 (Web UI) as `Done` after a final live walkthrough surfaced two real
  functional gaps (not the walkthrough itself failing) — those gaps got their own tickets
  (ERP-048, ERP-049) rather than reopening ERP-043.
- Fixed bugs before polish, per explicit instruction: ERP-048/ERP-049 implemented and shipped
  this session; ERP-050 (visual/UX redesign) opened as `Backlog` and deliberately deferred,
  with NotebookLM-style design research attached to its notes for whenever it's picked up.
- ERP-048's root cause was two-layered, not one: (1) no `GET /conversations` list-mine
  endpoint existed, so a fresh browser/device had no way to discover any conversation; (2)
  independently, clicking a sidebar conversation never fetched its history at all — it only
  switched the active ID. Both were fixed; #2 wasn't part of the original bug report but was
  found while investigating and is very likely what the reported screenshot actually showed
  (a "hi" conversation sitting inertly next to an empty chat pane).
- ERP-049 scope split cleanly along an existing seam: a `DocumentRecord` row is only created
  once `embed_and_persist` succeeds, so a failed/in-flight upload has nothing server-side to
  delete — "removing" one is a local-only dismiss, while a successfully ingested document gets
  a real `DELETE` endpoint that cleans up Postgres rows and FAISS vectors.
- Chose to actually remove FAISS vectors on delete (`FaissIndex.remove` over `remove_ids`,
  previously unused in this codebase) rather than rely on `retrieval.service.search`'s existing
  "drop fused hits with no matching chunk row" safety net — leaving stale vectors would waste
  `candidate_k` slots that real hits could otherwise use, a correctness/quality concern, not
  just a data-hygiene one.
- Deleted the now-unused `conversationsStore.ts` (and its test) entirely rather than leaving it
  as dead code, once the sidebar's conversation list moved to being server-sourced.

## Implementation Summary

Backend (all four modules touched: `app/generation/`, `app/ingestion/`, `app/embedding/`,
`app/main.py`):

- `app/generation/repository.py`: `list_conversations_for_owner`, `get_first_user_messages`
  (two queries, not N+1, via a min-`sequence`-per-conversation subquery join).
- `app/generation/service.py`: `list_conversations`. `app/generation/router.py`:
  `GET /conversations`. New schemas `ConversationSummary`/`ConversationListResponse`.
- `app/ingestion/repository.py`: `list_documents_for_owner`, `delete_document` (returns deleted
  `vector_id`s, or `None` if not found/not owned). New schemas `DocumentSummary`/
  `DocumentListResponse`.
- `app/embedding/index.py`: `FaissIndex.remove`, `OwnerFaissIndexStore.remove`.
- `app/embedding/service.py`: `delete_document_and_vectors` (Postgres delete + commit, then
  FAISS removal — ordered so a hypothetical FAISS failure leaves a harmless stale vector rather
  than an orphaned Postgres row).
- `app/ingestion/router.py`: new `documents_router` (`GET /documents`,
  `DELETE /documents/{document_id}`), registered in `app/main.py`.

Frontend (`frontend/src/`):

- Deleted `lib/conversationsStore.ts` and its test.
- `lib/recentItemsStore.ts` gained `remove(id)`.
- `lib/types.ts` gained `DocumentSummary`/`DocumentListResponse`/`ConversationSummary`/
  `ConversationListResponse`/`ConversationMessage`/`ConversationHistoryResponse`.
- `pages/ChatPage.tsx`: sidebar conversations and documents both now hydrate from the backend
  (`GET /conversations`, `GET /documents`) instead of `localStorage`; new `selectConversation()`
  loads a clicked conversation's full history via `GET /conversations/{id}` (restored messages
  have no citations — citations were never persisted per-message, a pre-existing, unchanged
  limitation).
- `pages/DocumentsPage.tsx`: rewritten with two sections — "In-progress uploads"
  (`localStorage`-tracked pending/processing/failed, with "Dismiss" for failed) and "Your
  documents" (server-sourced, with "Delete" calling the new endpoint). A job moves from one
  section to the other automatically once its poll reports `done`.

Tickets: ERP-043 marked `Done` with a Resolution note; ERP-048 and ERP-049 written and marked
`Done` with full Resolution notes; ERP-050 opened as `Backlog` with NotebookLM/2026
AI-citation-UI research attached (three-pane sources/chat/detail layout, clickable
citation-to-source-passage popovers as the highest-leverage visual gap, per live web search —
not decided or acted on, input for a future brainstorming pass only).

## Verification

Backend: `ruff check .` and `uv run mypy app/` both clean. Full suite `424 passed` (was 383),
97% coverage, against real Postgres/Redis containers (not mocks). Frontend: `tsc --noEmit` and
`oxlint src` both clean; `vitest run` `24 passed` (was 22, `conversationsStore.test.ts` removed,
`recentItemsStore.test.ts` gained `remove()` coverage).

Live end-to-end smoke test against the real local stack (Postgres/Redis/Ollama with
`gemma3:4b`/`nomic-embed-text:latest`, not fakes) — registered a throwaway user, uploaded a real
PDF, confirmed a grounded cited answer, confirmed `GET /conversations` and
`GET /conversations/{id}` correctly reproduced then fixed the login/history-reload bug,
confirmed `DELETE /documents/{id}` removed both the Postgres rows and the FAISS vectors (a
repeat retrieval query returned zero results, not just an empty list from a stale index), and
confirmed a repeat delete correctly 404s. Cleaned up: throwaway user and its rows deleted from
the dev Postgres, its FAISS index file removed, `.smoketmp/` scratch dir removed, dev servers
stopped.

## Blockers

None.

## Next Steps

- ERP-044, ERP-045, ERP-046 (document-scoped retrieval, answer feedback, relevance guardrail)
  remain `Backlog`, unstarted, unrelated to this session.
- ERP-050 (visual/UX redesign) is `Backlog`, deliberately deferred — scope to be decided via
  brainstorming when picked up, using this session's attached research as a starting point.
- The two live-created-and-cleaned-up smoke-test artifacts (throwaway user, FAISS index file)
  were local-dev-only — no live-deployment cleanup is needed from this session.
