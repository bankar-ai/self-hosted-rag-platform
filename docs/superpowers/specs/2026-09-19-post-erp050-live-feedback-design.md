# Design: Post-ERP-050 Live Feedback Batch (ERP-067 – ERP-074)

Date: 2026-09-19

## Problem

Live-testing the ERP-050 source panel surfaced eight distinct issues/requests, spanning both
the Chat page and the Documents page. Captured here as one spec since several share root
causes or touch the same files, but each keeps its own ticket for tracking:

1. **ERP-067** — `SourcePanel` renders the chunk's raw stored text verbatim, so PDF-derived
   markdown/HTML artifacts (`**bold**`, `#####` headers, `<mark>`, `<u>`) show as literal
   syntax instead of being rendered.
2. **ERP-068** — no way to copy an answer, a whole conversation, or a source panel's text to
   the clipboard.
3. **ERP-069** — confirmed hallucination: asked to explain a citation, the model stated a
   specific date ("July 21, 2026") that does not appear anywhere in the source PDF.
4. **ERP-070** — dropping or selecting files on the Documents page uploads immediately; the
   user wants an explicit "Upload" step, and the ability to review/remove staged files first.
5. **ERP-071** — no way to select and delete multiple documents at once, and no confirmation
   dialog before any delete (single or bulk).
6. **ERP-072** — dismissing a failed upload only clears it client-side; the backend's in-memory
   job record and its temp file on disk (deliberately retained for `Retry`, per ERP-053) are
   never cleaned up — an unbounded disk leak on a resource-constrained VM.
7. **ERP-073** — if the network drops mid-upload (before a job is even created), the file
   silently disappears from the "uploading" list with no error and no way to retry from the UI.
8. **ERP-074** — `Button`'s hardcoded default `bg-brand`/`text-white` classes fight with
   per-call-site color overrides (`bg-white text-red-600`, etc.) under Tailwind's utility-class
   cascade, which resolves by stylesheet order, not JSX class-string order — this is the
   confirmed root cause of the Documents page's invisible Delete/Retry/Dismiss button text
   (white text left in effect over a white background override).

## Decisions made (from live discussion)

- **ERP-067**: full proper rendering, not a stripped-down plain-text fallback — the source
  content's headers/emphasis carry real structure worth preserving. New dependencies approved:
  `react-markdown` (renders directly to React elements, no `dangerouslySetInnerHTML` — the
  safer default for untrusted content per current guidance) + `rehype-raw` (interprets the
  embedded `<mark>`/`<u>` tags PDF parsing sometimes emits) + `rehype-sanitize` (keeps that
  raw-HTML path safe against arbitrary/malicious embedded markup, since PDF content isn't
  fully trusted input). Scoped to `SourcePanel` only — the chat's own `markdownLite.tsx` stays
  as is, since LLM output is a small, controlled, known subset (bold + lists), not arbitrary
  parsed-document content.
- **ERP-068**: three separate copy affordances, not one generic "copy" button — per-message
  (assistant answers), per-conversation (full transcript), and the source panel's chunk text.
  No new dependency (`navigator.clipboard.writeText`).
- **ERP-069**: a prompt-only fix. `app/generation/prompt.py`'s `SYSTEM_PROMPT` gains an explicit
  instruction: never state a specific date, number, or fact unless it is verbatim present in
  the provided context; when a follow-up asks for a detail the context doesn't specify, say so
  rather than inferring or inventing one. Verified by reproducing the exact failing case (same
  PDF, same follow-up question) before and after.
- **ERP-070**: files chosen via drag-drop or the file picker are staged (shown in a list with
  filename + a per-file remove ✕), not uploaded immediately. An "Upload N files" button starts
  the actual upload for everything currently staged. Oversized files are still rejected at
  staging time (unchanged behavior, just earlier in the flow). Multi-file support already
  exists end-to-end (`multiple` input attribute, `Promise.all` upload) and needs no change.
- **ERP-071**: a checkbox per document row + "select all" + a "Delete selected (N)" button,
  reusing the existing single-delete endpoint via `Promise.all`. A native `window.confirm`
  (not a custom modal — matches this app's existing dialog pattern, used already for rename)
  gates every delete, single or bulk, naming exactly what will be deleted.
- **ERP-072**: new `DELETE /ingestion/jobs/{job_id}` endpoint — owner-scoped like every other
  endpoint — deletes the in-memory `JobRecord` and unlinks its temp file (and parent dir if now
  empty, mirroring `run_ingestion_job`'s existing success-path cleanup). The frontend's
  "Dismiss" button calls this instead of only clearing local state. Only valid for a `FAILED`
  job (mirroring `retry_job`'s existing status check) — a `PENDING`/`PROCESSING` job has no
  "dismiss" affordance today and this doesn't add one; a `DONE` job has no `JobRecord` presence
  worth dismissing (it's already surfaced via `serverDocuments` instead).
- **ERP-073**: `uploadOne`'s failure path (currently `.catch(() => null)` then a silent early
  `return`) instead marks that upload as failed in a new state bucket, shown with an error
  message and a "Try again" button that re-calls `uploadOne` with the same in-memory `File`
  object (still held by the browser — no re-selection needed, since the failure happened before
  any bytes reached the server).
- **ERP-074**: `Button` gains a `variant` prop (`"primary" | "outline" | "danger"`, defaulting
  to `"primary"` — today's `bg-brand`/`text-white` look, so every existing bare `<Button>` call
  site needs zero changes). `variant="outline"` and `variant="danger"` each carry a complete,
  non-conflicting class set (no shared background/text-color utility with `"primary"`) so
  there's nothing left for the cascade to fight over. `DocumentsPage.tsx`'s Delete
  (`bg-white text-red-600 ring-1 ring-red-200 hover:bg-red-50`) becomes `variant="danger"`;
  Retry/Dismiss (`bg-white text-slate-700 ring-1 ring-slate-300 hover:bg-slate-100`) become
  `variant="outline"`.

## Architecture

### ERP-067 — SourcePanel rendering

`SourcePanel.tsx`'s body (currently `<p className="whitespace-pre-wrap">{state.text}</p>`)
becomes `<ReactMarkdown remarkPlugins={[]} rehypePlugins={[rehypeRaw, rehypeSanitize]}>{state.text}</ReactMarkdown>`,
wrapped in a container that gives the rendered headers/lists/emphasis reasonable spacing (a
small set of Tailwind `prose`-like utility overrides via `[&_h1]:...` arbitrary variants, or a
handful of explicit child-element classes — no new CSS framework). `rehype-sanitize`'s default
schema is extended to explicitly allow `mark` and `u` (not in its default allow-list) so the
two tags this codebase's PDFs actually emit survive sanitization instead of being stripped.

### ERP-068 — Copy affordances

- Per-message: a small copy icon/button next to the existing 👍/👎 row on each assistant
  message in `ChatPage.tsx`, copying `message.content` (the raw text, not re-rendered markdown)
  via `navigator.clipboard.writeText`. A brief "Copied" state (e.g. icon swap for ~1.5s) confirms
  the action — no toast library, just local component state.
- Per-conversation: one button near "New chat" or the conversation header, building a plain-text
  transcript (`"You: {content}\n\nAssistant: {content}\n\n..."` for every message in `messages`)
  and copying it.
- Source panel: a copy button in `SourcePanel`'s header, copying `state.text` (the chunk body)
  once loaded — disabled/absent in the loading/error/not-found states, since there's nothing to
  copy yet.

### ERP-069 — Prompt fix

Single addition to `SYSTEM_PROMPT` in `app/generation/prompt.py`. No schema/API change. Verified
via a live reproduction: re-ingest the exact PDF from this conversation, ask the same follow-up
("explain this July 21, 2026 is the date of..."), confirm the fixed prompt no longer asserts an
unsupported date.

### ERP-070 — Staged upload

`DocumentsPage.tsx` gains `stagedFiles: File[]` state. `handleDrop`/`handleFileInputChange` call
a new `stageFiles(files: File[])` (rejects oversized files immediately, same
`MAX_UPLOAD_SIZE_BYTES` check as today, just moved earlier) instead of calling `handleFiles`
directly. A new section renders `stagedFiles` as a list (filename + a per-item ✕ removing just
that file from staging) plus an "Upload N files" button that calls the existing `handleFiles`
with the current `stagedFiles` and then clears staging. Everything downstream of `handleFiles`
(upload progress, job polling, in-progress list) is unchanged.

### ERP-071 — Bulk delete + confirmation

`DocumentsPage.tsx` gains `selectedDocumentIds: Set<string>` for the "Your documents" list (a
checkbox per row, a "select all" checkbox above the list) and a "Delete selected (N)" button,
visible only when the set is non-empty. Both single delete and bulk delete route through one
new `confirmAndDelete(documentIds: string[])` helper: builds a confirmation message naming the
file(s) (by filename, looked up from `serverDocuments`), shows `window.confirm(...)`, and only
on confirmation calls `Promise.all` over `DELETE /documents/{id}` for each ID — reusing the
existing single-delete endpoint unchanged (no new bulk-delete backend endpoint needed).

### ERP-072 — Dismiss cleanup endpoint

New `DELETE /ingestion/jobs/{job_id}` on the existing `app/ingestion/router.py`. New
`app/ingestion/jobs.py` function `delete_job(job_id: str, owner_id: uuid.UUID) -> bool` —
`False` (mapped to `404`) unless the job exists, is owned by the caller, and is `FAILED`
(mirroring `retry_job`'s exact guard); on success, removes the `JobRecord` from the in-memory
dict and unlinks its temp file (reusing the same `Path(pdf_path).unlink(missing_ok=True)` +
best-effort parent-dir cleanup pattern `run_ingestion_job` already uses on its success path).
`DocumentsPage.tsx`'s `dismissInProgress` becomes async, calling this endpoint before (or
alongside) its existing local-store cleanup — a failed network call still clears the local
state (best-effort backend cleanup, never blocking the user's own view from updating).

### ERP-073 — Upload-failure UI

`uploadOne`'s `.catch(() => null)` branch, instead of silently returning, marks that entry in
`uploadsInFlight` (extended with a `status: "uploading" | "failed"` field, default
`"uploading"`) as `"failed"` rather than removing it. The rendered list shows a failed entry
with an error message and a "Try again" button calling `uploadOne(file)` again — the original
`File` object is still in scope (captured in the closure / re-derived from the same staged-file
entry), so no re-selection is needed.

### ERP-074 — Button variant

`components/ui/button.tsx` gains a `variant?: "primary" | "outline" | "danger"` prop (default
`"primary"`). Each variant is a complete, self-contained class string (background, text color,
hover, focus states) with no shared color utility across variants, so the cascade-order bug
that caused ERP-074's own symptom cannot recur regardless of what a caller also passes via
`className` (which stays for structural/spacing overrides like `w-full`, `mb-4` — never color).
`DocumentsPage.tsx`'s Delete button → `variant="danger"`; Retry/Dismiss → `variant="outline"`.
No other call site changes (every existing `<Button>` with no `variant` keeps today's look
exactly).

## Testing

- ERP-067: extend `SourcePanel.test.tsx` with a case asserting `**bold**`/`# heading`/`<mark>`
  render as real elements, not literal syntax, and that a disallowed tag (e.g. a stray
  `<script>`, to prove sanitization) is stripped.
- ERP-068: new tests for each copy button (mock `navigator.clipboard.writeText`, assert it's
  called with the expected text).
- ERP-069: no automated test (prompt-engineering changes aren't reliably unit-testable against
  a live LLM) — verified via the live reproduction described above, same as ERP-046's
  calibration approach.
- ERP-070/071/072/073: new `DocumentsPage.tsx` tests (this page currently has none — a first
  test file, following the same component-testing patterns already used for `SourcePanel`)
  covering: staging doesn't call the upload endpoint until "Upload" is clicked; bulk delete
  calls `DELETE` for every selected ID after confirmation, and not at all if `window.confirm`
  returns false; dismiss calls the new endpoint; a rejected/failed upload shows a retry button
  that re-attempts the same file. Backend: new tests for
  `DELETE /ingestion/jobs/{job_id}` (happy path, 404 for unknown/cross-owner/non-failed job).
- ERP-074: extend or add a `button.test.tsx` (none exists today) asserting each variant's
  class string, plus a visual sanity check that no variant contains both a background and text
  color also present in another variant.

## Rollout

Same workflow as ERP-050: implement via subagent-driven development → backend
`ruff`/`mypy`/`pytest`, frontend `tsc`/`oxlint`/`vitest` → commit → PR → CI → merge to `develop`
→ deploy (VM `git pull` + `systemctl restart`; frontend `vercel --prod --scope bankar-ai`) →
live-verify (including re-testing the exact hallucination scenario from this conversation) →
close out `.ai/tickets/ERP-067.md` through `ERP-074.md` and update `current-state.md`.
