# Design: Visual/UX Redesign — Source Panel + Three-Pane Layout (ERP-050)

Date: 2026-09-19
Ticket: ERP-050

## Problem

Surfaced during ERP-043's live UI review and confirmed again in this session: the chat UI has
two related gaps.

1. **No source traceability.** An answer's citations render as a flat, plain-text list below the
   answer (`[1] filename, p.3`, expandable to a one-line relevance score) — there is no way to
   see the actual passage a citation refers to without leaving the app to reopen the original
   file (which, separately, we don't even keep — see below). This is the concrete gap named by
   this ticket's own research: current-generation RAG UIs (NotebookLM, Perplexity, Claude,
   ChatGPT search) all shorten the distance between a claim and the evidence backing it; ours
   doesn't.
2. **Basic visual design.** `frontend/src/index.css` is a bare `@import "tailwindcss"` with zero
   customization — every color/spacing decision is an ad hoc Tailwind default picked file by
   file during the original ERP-043 build, not a deliberate design.

## Constraints and decisions made during brainstorming

- **No original PDF storage exists.** Ingestion parses an uploaded PDF into text chunks
  (persisted to Postgres with page/section metadata) and discards the uploaded bytes afterward
  (ERP-053 cleaned up what had been a leak of temp upload files). A "highlight the passage inside
  the original PDF page" viewer is therefore out of scope for this pass — it would require new
  storage infra (e.g. a GCS bucket) and page-coordinate extraction our parsers don't currently
  do. **Decision: the source panel shows the already-stored extracted chunk text, not a PDF
  rendering.** This costs no new infrastructure and no new storage.
- **Cited chunk only, no sibling/section expansion.** `get_sibling_chunks` (built for ERP-016)
  could pull in surrounding chunks from the same section, but this was deliberately deferred as
  unnecessary complexity for v1 — confirmed cheap to add later since it's scoped per-document
  (a citation's siblings are looked up only within that one document, regardless of how many
  total documents the caller has uploaded — this was a specific feasibility question raised
  during brainstorming and confirmed not a concern either at 1 or 100 documents).
- **Chunk text fetched on demand, not embedded in every citation.** `Citation` (the schema
  `GenerationResponse`/the streaming `citations` event returns) stays metadata-only
  (`chunk_id`, `document_id`, `page_start`/`page_end`, `section_path`, `source_filename`,
  `score`, `reranked`). The panel fetches the actual `text` only when opened, via a new endpoint
  — keeps every streamed answer's payload lean regardless of how many citations it carries.
- **Both citation triggers open the same panel.** The existing citation list below an answer and
  a new clickable `[n]` marker inline in the answer text both open the same source panel for the
  same citation — matching the actual NotebookLM/Perplexity pattern (inline marker = "show me
  where this specific claim came from"; list = "show me everything this answer used").
- **Three-pane layout, not a "Studio" clone.** NotebookLM's third pane generates study
  guides/FAQs/outlines — a real new backend capability nobody asked for and out of scope here.
  The right-hand pane in this redesign is specifically the source panel above, not a
  general-purpose "generated artifacts" panel.
- **Layout restructuring has no infra cost.** Confirmed during brainstorming: a three-column
  layout calls the same backend endpoints, at the same frequency, as the current two-column
  layout — `GET /documents`, `GET /conversations`, `POST /generation/query/stream`, plus the one
  new chunk-fetch endpoint below. The `e2-micro` VM / Neon / Upstash see zero difference. The
  only new infra-relevant addition is the chunk-fetch endpoint itself, which is a single indexed
  Postgres read (no new tables, no new external calls).

## Architecture

### Backend: one new endpoint

`GET /documents/{document_id}/chunks/{chunk_id}` on the existing `app/ingestion/router.py`
(alongside `GET /documents`, `DELETE /documents/{document_id}`), following the same
`get_current_user` owner-scoping pattern as every other endpoint in this router.

- New `get_chunk_by_document_and_owner(session, document_id, chunk_id, owner_id) -> ChunkRecord | None`
  in `app/ingestion/repository.py` — a single query joining `ChunkRecord` to `DocumentRecord` on
  `document_id`, filtered by `DocumentRecord.owner_id == owner_id`. Returns `None` (mapped to
  `404`) if the chunk doesn't exist, doesn't belong to the given document, or the document isn't
  owned by the caller — one 404 for all three cases, consistent with how this codebase already
  handles cross-owner access everywhere else (no information disclosure about *why* it's a 404).
- New `ChunkDetailResponse` schema (`app/ingestion/schemas.py`): `chunk_id`, `document_id`,
  `text`, `section_path`, `page_start`, `page_end`, `source_filename`.
- No new dependency, no schema/migration change (reads existing `chunks`/`documents` tables).

### Frontend: component split + new panel

`ChatPage.tsx` (currently ~470 lines, doing sidebar + message list + input + streaming all in
one file) is split so each new/changed piece has one clear job:

- **`components/Sidebar.tsx`** (new, extracted) — recent conversations + documents-with-checkboxes,
  exactly the content `ChatPage.tsx` renders today, unchanged in behavior. Takes the existing
  props/callbacks (`recentConversations`, `documents`, `deselectedDocumentIds`,
  `onSelectConversation`, `onRename`, `onToggleDocument`, `onNewConversation`) rather than owning
  any of that state itself — `ChatPage.tsx` keeps owning state, same as now, just stops also
  owning the JSX for it.
- **`components/SourcePanel.tsx`** (new) — props: `citation: Citation | null`, `onClose: () => void`.
  Renders nothing when `citation` is `null` (this is what makes the layout two columns when idle
  and three when a citation is open, per the brainstorming decision above). When a `citation` is
  provided: fetches `GET /documents/{document_id}/chunks/{chunk_id}` on mount/citation-change,
  shows a loading skeleton while in flight, then either the chunk text (header: filename, "p.
  {start}" or "p. {start}-{end}", section path breadcrumb; body: the chunk's full `text`) or one
  of two distinct error states — a 404 renders "This source is no longer available" (the
  document/chunk was deleted after the answer was generated, a real possibility since ERP-049),
  any other failure renders a generic retry-able error, not a crash.
- **`lib/markdownLite.tsx`** — `renderMarkdownLite` gains two new parameters:
  `citations: Citation[]` and `onCitationClick: (citation: Citation) => void`. After existing
  bold/list rendering, a regex pass over the remaining text nodes finds `[`, digits, `]` and,
  when the number matches a 1-indexed position in `citations`, replaces it with a clickable
  `<button>` (styled as a small superscript-like chip) calling `onCitationClick`; a `[n]` with no
  matching citation index renders as plain text unchanged (defensive — matches how the existing
  citation-list rendering already only shows citations that exist).
- **`ChatPage.tsx`** becomes the orchestrator: keeps all existing state as-is, gains
  `selectedCitation: Citation | null`, renders three flex/grid children — `<Sidebar>`, the
  existing message-list + input column, and `<SourcePanel citation={selectedCitation}
  onClose={() => setSelectedCitation(null)} />`. The citation list below each answer and
  `renderMarkdownLite`'s new inline markers both call `setSelectedCitation`.
- **`lib/types.ts`** gains `ChunkDetail` (mirroring the new backend response) and `Citation` is
  unchanged (still metadata-only, per the on-demand-fetch decision above).

### Cosmetic layer

`frontend/src/index.css` gains a small `@theme` block (Tailwind v4's CSS-first theming — no new
dependency, no config file needed) defining a handful of custom tokens: an accent color (replacing
the current plain `slate-900`/`emerald-500` ad hoc choices with one deliberate accent used
consistently for primary actions, active sidebar state, and the send button), and reuses
Tailwind's existing spacing/radius scale rather than inventing a new one. Applied to: message
bubbles, sidebar active/hover states, the new `SourcePanel`, and `components/ui/button.tsx`'s
existing variants — a consistency pass over what's already there, not a new component library.
Explicitly out of scope: dark mode, animations/transitions beyond what Tailwind's utilities give
for free (e.g. panel slide-in), and any change to `LoginPage.tsx`/`DocumentsPage.tsx` beyond
reusing the same new `@theme` tokens if they already use the affected utility classes (no
dedicated redesign pass for those two pages — this ticket is scoped to the chat experience,
matching where the original complaint and research came from).

## Error Handling

- Backend: unknown/cross-owner chunk or document → `404`, same convention as
  `GET /conversations/{id}`, `DELETE /documents/{id}`. No new exception types needed — reuses the
  existing "return `None`, router raises `HTTPException(404)`" pattern already used throughout
  `app/ingestion/router.py`.
- Frontend: `SourcePanel` distinguishes a `404` (source genuinely gone — expected, not a bug) from
  any other failure (network error, `5xx` — offer a retry). Neither state blocks the rest of the
  chat UI; the panel is the only thing that shows an error.

## Testing

- Backend: new tests in `tests/ingestion/test_router.py` (or a focused new test module if that
  file is already large) covering the happy path (real ingested chunk, correct text/metadata
  returned), unknown `chunk_id` → 404, unknown `document_id` → 404, and a cross-owner chunk → 404
  (owner A's chunk requested with owner B's token) — mirroring the existing cross-owner test
  pattern already used for retrieval/conversations in this repo.
- Frontend: new `components/SourcePanel.test.tsx` covering loading → success, loading → 404
  ("not available") message, loading → generic error (retry affordance shown). Extends
  `lib/markdownLite.test.tsx` with cases for a `[1]` marker becoming a clickable element that
  calls the provided callback with the right citation, and a `[9]` with no matching citation
  index rendering as plain text. `ChatPage.tsx`'s own integration (the three-pane layout actually
  wiring together, panel opening state) is verified live against the local dev stack rather than
  unit-tested — consistent with how ERP-044's `ChatPage.tsx` changes were verified (no existing
  SSE-mocking test harness for this page, and building one is a separable, bigger effort not
  worth taking on as a side effect of this ticket).
- No backend migration, no new dependency (frontend or backend).

## Rollout

Same workflow as every other ticket this project: implement → `ruff`/`mypy`/`pytest` (backend),
`tsc`/`oxlint`/`vitest` (frontend) → commit → PR → CI `test` check → merge to `develop` → deploy
(VM `git pull` + `systemctl restart`, no migration needed; frontend `vercel --prod`) → live-verify
against the production deployment with a real ingested document and a real generation query,
confirming the panel opens with correct text and a deleted-document citation shows the "no longer
available" state → clean up any test data created → close out `.ai/tickets/ERP-050.md` and update
`.ai/memory/current-state.md`.
