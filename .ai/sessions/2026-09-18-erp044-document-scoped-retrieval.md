# Session — Document-Scoped Retrieval (ERP-044)

Date: 2026-09-18
Tickets Touched: ERP-044

## Decisions

- Resolved ERP-044's open scoping questions before writing production code, backed by a
  standalone experiment against real `faiss`: `IndexIDMap2` + `IDSelectorBatch` +
  `SearchParameters(sel=...)` gives true search-time filtering, not a post-hoc filter (which
  the ticket itself flagged as risking a silently-dropped real match outside the top-k).
- Filtering happens before RRF fusion, on both retrieval legs (FAISS and BM25) — not just one.
- `document_ids=None` (field omitted) is byte-for-byte the existing "search everything owned"
  behavior; `document_ids=[]` (explicit empty list) means "search nothing," short-circuiting
  before even the cache lookup. These are deliberately different states, not the same thing.
- The retrieval cache key now includes the sorted `document_ids` set so a scoped and unscoped
  query for identical text never collide.
- Frontend uses an opt-out selection model (checked by default, track only deselections) rather
  than syncing an explicit "selected" set against the document list on every refresh — a newly
  uploaded document is automatically included with no extra wiring.

## Implementation Summary

Backend: `app/embedding/index.py` (`FaissIndex.search`/`OwnerFaissIndexStore.search` gain
`allowed_vector_ids`), `app/ingestion/repository.py` (`search_chunks_by_text` gains
`document_ids`; new `get_vector_ids_for_documents`), `app/retrieval/service.py` (`search()` gains
`document_ids`, cache key updated, session scope widened to run the FAISS search inside it too),
`app/retrieval/schemas.py`/`router.py`, `app/generation/schemas.py`/`service.py`/`router.py` — the
full public surface (`RetrievalQuery`, `GenerationQuery`, both generation endpoints) now accepts
and threads through `document_ids`.

Frontend: `frontend/src/pages/ChatPage.tsx` — the document sidebar's plain list became a checkbox
list (`deselectedDocumentIds: Set<string>` state, opt-out model); `sendMessage()` now sends the
currently-checked set as `document_ids` on every query. `frontend/src/lib/types.ts`'s
`GenerationRequest` gained the matching optional field.

## Verification

- Backend: `ruff check .` / `mypy app/` clean; `pytest -q` 502 passed (was 485) — new coverage
  across `tests/embedding/`, `tests/ingestion/`, `tests/retrieval/`, `tests/generation/`,
  including a real end-to-end router test (two PDFs uploaded, scoped query confirmed to exclude
  the other document).
- Frontend: `npm run build` (`tsc -b && vite build`) and `npm run lint` (`oxlint`) both clean;
  `npm test` (vitest) 29 passed (unchanged — `ChatPage.tsx` has no prior unit test coverage to
  extend; verified live instead, see below).
- PR #48 → `develop`, CI `test` check passed, merged (fast-forward, `f43acf5`/`0bc3ef4` →
  `28d0c60`).
- Deployed: backend via `gcloud compute ssh` → `git pull origin develop` + `systemctl restart
  rag-platform.service` (no DB migration — no schema change this ticket); frontend via `npx
  vercel --prod` from `frontend/`.
- **Live-verified against the production deployment** (`https://34-31-5-88.sslip.io`): registered
  a throwaway user, uploaded two PDFs with mutually-exclusive unique content ("Alpha" doc /
  "Beta" doc). `POST /retrieval/query` scoped to Alpha asking about Beta's content →
  `{"results": []}`; scoped to Beta → found it; unscoped (both) → found it; `document_ids: []` →
  `{"results": []}`. All four match the design exactly. A full `POST /generation/query/stream`
  scoped to the Beta document produced a correct, grounded, cited answer ("...lives in the Beta
  document [1].") citing only that document's chunk. Cleaned up: deleted the throwaway user via
  `DELETE /admin/users/{id}` (204), confirmed via `GET /admin/users` that only the two legitimate
  accounts (`ops-admin`, the real user's own `pankajbankar4975@gmail.com`) remain.

## Blockers

None.

## Next Steps

- ERP-050 (visual/UX redesign) remains the one explicitly-deferred item from the prior session's
  "go ahead" — its own brainstorming/design pass, not started.
- `.ai/memory/current-state.md`'s "Next Planned Work" section had drifted slightly stale (a note
  claiming the ERP-051-059 batch wasn't deployed, when the live VM was actually already at
  ERP-066 by the time this session checked) — worth a closer audit next time a batch of
  `current-state.md` entries piles up without a deploy-status re-check.
