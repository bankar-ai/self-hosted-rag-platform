# ERP-086 + ERP-085 Live Bugfixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two confirmed live bugs: (1) the Cloud Run docling service OOM-killing on large
scanned PDFs and surfacing a raw 503 to users (ERP-086), and (2) two mobile-viewport layout bugs
in `AppShell`'s header and `SourcePanel` (ERP-085).

**Architecture:** ERP-086 is two independent changes — a friendlier error message at the
`DoclingServiceError` raise site, and a Cloud Run redeploy with more memory (the redeploy is a
live/billable infra action and requires explicit user confirmation before running, not just
before merging code). ERP-085 is two independent CSS/layout changes in the frontend, no new
components: `AppShell`'s header needs to wrap instead of forcing horizontal overflow, and
`SourcePanel` needs `overflow-x` contained and its header controls kept reachable.

**Tech Stack:** Python 3.12, FastAPI, pytest, httpx (backend); React 19, TypeScript, Tailwind v4,
Vitest + React Testing Library (frontend).

**Spec:** `.ai/tickets/ERP-086.md`, `.ai/tickets/ERP-085.md` (both carry root-cause findings from
live investigation — no separate design spec needed for fixes this scoped).

## Global Constraints

- Never use `pip install` — all Python deps via `uv`.
- No `print()` in application code — use the existing `logging` convention (module-level
  logger, `logger.exception` on non-re-raising excepts).
- Conventional Commits (`fix:`, `docs:`) for every commit.
- Frontend: match existing Tailwind utility-class style in the touched files — no new CSS files,
  no new dependencies.

---

### Task 1: Friendlier error message when the docling service call fails

**Files:**
- Modify: `app/ingestion/cloud_run_client.py:70-72`
- Test: `tests/ingestion/test_cloud_run_client.py`

**Interfaces:**
- Consumes: nothing new — `DoclingServiceError` (already defined at `cloud_run_client.py:23`)
  keeps its existing signature (`Exception` subclass, single string message).
- Produces: `DoclingServiceError` raised for an HTTP-status failure now carries a fixed,
  user-safe message instead of the raw `httpx` exception text. `app/ingestion/jobs.py:111`
  (`_jobs[job_id].error = str(exc)`) is unchanged — it already just stringifies whatever
  exception it catches, so this fix only needs to change what message the exception carries.

- [ ] **Step 1: Write the failing test**

Add to `tests/ingestion/test_cloud_run_client.py`:

```python
def test_call_docling_service_raises_friendly_message_on_non_200_response(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-fake-content")

    def handler(request: httpx.Request) -> httpx.Response:
        if "identity" in str(request.url):
            return httpx.Response(200, text="fake-token")
        return httpx.Response(503, text="Service Unavailable")

    with _stub_httpx_client(handler):
        with pytest.raises(DoclingServiceError) as exc_info:
            call_docling_service(str(pdf_path), _settings())

    message = str(exc_info.value)
    assert "too large or complex" in message
    assert "Service Unavailable" not in message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_cloud_run_client.py::test_call_docling_service_raises_friendly_message_on_non_200_response -v`
Expected: FAIL — current code raises `DoclingServiceError(str(exc))`, which contains the raw
httpx status-error text, not "too large or complex".

- [ ] **Step 3: Write minimal implementation**

In `app/ingestion/cloud_run_client.py`, replace the existing `except httpx.HTTPError` block
(lines 70-72) with two blocks, catching the more specific `HTTPStatusError` first (it's a
subclass of `HTTPError`, so order matters):

```python
    except httpx.HTTPStatusError as exc:
        logger.exception("Docling Cloud Run service returned an error response")
        raise DoclingServiceError(
            "This document could not be processed by the quality parser -- it may be too "
            "large or complex (e.g. a long scanned document). Try a smaller file, or contact "
            "support if this keeps happening."
        ) from exc
    except httpx.HTTPError as exc:
        logger.exception("Docling Cloud Run service call failed")
        raise DoclingServiceError(str(exc)) from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingestion/test_cloud_run_client.py -v`
Expected: all tests in this file PASS, including the new one and the pre-existing
`test_call_docling_service_raises_on_non_200_response` (which only asserts
`pytest.raises(DoclingServiceError)`, no message check, so it still passes unchanged).

- [ ] **Step 5: Run the full backend test suite and lint**

Run: `uv run pytest`, `uv run ruff check .`, `uv run mypy app`
Expected: all PASS/clean (no other code references the old raw-message behavior).

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/cloud_run_client.py tests/ingestion/test_cloud_run_client.py
git commit -m "fix: surface a friendly error when the docling service call fails (ERP-086)"
```

---

### Task 2: Increase Cloud Run docling service memory to fix OOM-kills

**Files:**
- Modify: `deploy/cloud_run_docling/README.md:19-29` (the documented `gcloud run deploy` command)

**Interfaces:**
- Consumes: nothing (infra-only change, no application code).
- Produces: nothing consumed by other tasks.

**IMPORTANT:** the actual `gcloud run deploy` redeploy is a live, billable infrastructure change
against the real GCP project (`self-hosted-rag-platform`). Update the documented command in this
task, but **stop and get explicit user confirmation before running `gcloud run deploy` for
real** — do not run it automatically as part of this task.

- [ ] **Step 1: Update the documented deploy command**

In `deploy/cloud_run_docling/README.md`, change `--memory 4Gi` to `--memory 8Gi` in the `gcloud
run deploy` command block (README.md:19-29). Add a one-line note above the command explaining
why:

```markdown
## Deploy

`--memory 8Gi` (raised from 4Gi 2026-09-23, ERP-086): a large scanned document OOM-killed the
container at 4Gi in production, surfacing as a raw 503 to the user. See
`.ai/tickets/ERP-086.md`.

```
gcloud run deploy self-hosted-rag-platform-docling \
  --source deploy/cloud_run_docling \
  --region us-central1 \
  --no-allow-unauthenticated \
  --memory 8Gi \
  --cpu 2 \
  --timeout 600 \
  --concurrency 1 \
  --min-instances 0 \
  --max-instances 3 \
  --project self-hosted-rag-platform
```
```

- [ ] **Step 2: Commit the doc change**

```bash
git add deploy/cloud_run_docling/README.md
git commit -m "docs: bump docling Cloud Run memory to 8Gi in deploy command (ERP-086)"
```

- [ ] **Step 3: STOP — ask the user before running the real redeploy**

Do not run `gcloud run deploy` in this task. Surface to the user: "Ready to redeploy
`self-hosted-rag-platform-docling` with `--memory 8Gi` — this is a live, billable change against
the real GCP project. Confirm before I run it." Only run it after explicit confirmation, then
update `D:\github-projects\gcp-deployment-tracker.md`'s docling row with the new revision and
memory value (same pattern as the existing ERP-076 redeploy note there).

---

### Task 3: Fix `AppShell` header overflow/collision on narrow viewports

**Files:**
- Modify: `frontend/src/components/AppShell.tsx`
- Test: Create `frontend/src/components/AppShell.test.tsx`

**Interfaces:**
- Consumes: `useAuth()` from `../lib/AuthContext` (unchanged usage).
- Produces: nothing consumed by other tasks — `AppShell` is a leaf layout component.

Root cause: the `<header>` is `flex items-center justify-between` (a single row, no wrapping).
Its two children (the title `<span>` and the `<nav>`) both have `flex-shrink` default to 1 but
`min-width: auto`, which prevents them shrinking below their own content width — so on a narrow
viewport the combined intrinsic width exceeds the viewport, and the row overflows horizontally
instead of reflowing, which is what produces both the title/nav visual collision and the
page-level horizontal scroll.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/AppShell.test.tsx`. This project's convention (see
`frontend/src/components/AuthGuard.test.tsx`) wraps components in the real `AuthProvider` with
`fetch` stubbed, rather than mocking `../lib/AuthContext` directly — follow that pattern here:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../lib/AuthContext";
import AppShell from "./AppShell";

describe("AppShell", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("allows the header to wrap instead of forcing horizontal overflow", () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <AppShell>
            <div>content</div>
          </AppShell>
        </AuthProvider>
      </MemoryRouter>
    );
    const header = screen.getByText("Self-Hosted RAG Platform").closest("header");
    expect(header).not.toBeNull();
    expect(header?.className).toContain("flex-wrap");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/AppShell.test.tsx`
Expected: FAIL — current `header` className has no `flex-wrap`.

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/components/AppShell.tsx`, change the header and nav elements:

```tsx
      <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-slate-200 bg-white px-6 py-3">
        <span className="text-lg font-semibold tracking-tight text-slate-900">
          Self-Hosted RAG Platform
        </span>
        <nav className="flex flex-wrap items-center gap-3">
```

(Only the `className` strings on the existing `<header>` and `<nav>` elements change — `flex
items-center justify-between` becomes `flex flex-wrap items-center justify-between gap-x-4
gap-y-2`, and `flex items-center gap-3` becomes `flex flex-wrap items-center gap-3`. No other
JSX changes.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/AppShell.test.tsx`
Expected: PASS.

- [ ] **Step 5: Run the full frontend test suite, typecheck, lint, build**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npx oxlint . && npx vite build`
Expected: all PASS/clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/AppShell.tsx frontend/src/components/AppShell.test.tsx
git commit -m "fix: let the header wrap instead of overflowing on narrow viewports (ERP-085)"
```

---

### Task 4: Fix `SourcePanel` horizontal overflow and unreachable controls

**Files:**
- Modify: `frontend/src/components/SourcePanel.tsx`
- Test: Modify `frontend/src/components/SourcePanel.test.tsx`

**Interfaces:**
- Consumes: `Citation`/`ChunkDetail` types (unchanged), `CopyButton` (unchanged usage).
- Produces: nothing consumed by other tasks.

Root cause: the `<aside>` sets `overflow-y-auto` but never sets `overflow-x`. Per the CSS spec,
when one of `overflow-x`/`overflow-y` is non-`visible` and the other is left as `visible`, the
`visible` one is computed as `auto` instead — so this element is implicitly horizontally
scrollable as a whole. Any content inside wider than the panel (e.g. a long unbroken token in
extracted PDF text) drags the *entire* panel sideways when scrolled, including the header's
Copy/Close controls, instead of just wrapping within the text area.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/components/SourcePanel.test.tsx`, inside the existing `describe("SourcePanel", ...)` block. The file already has a module-level `citation: Citation` fixture (top of
file) and a `404` response is the simplest stub that doesn't require `waitFor` for this
particular assertion (the className is on the `<aside>` itself, present immediately, regardless
of fetch state) — reuse both exactly as the existing `"shows a 'no longer available' message on
a 404"` test does:

```tsx
  it("contains horizontal overflow instead of letting the whole panel scroll sideways", () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));

    const { container } = render(<SourcePanel citation={citation} onClose={() => {}} />);

    const panel = container.querySelector('[role="dialog"]');
    expect(panel).not.toBeNull();
    expect(panel?.className).toContain("overflow-x-hidden");
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/SourcePanel.test.tsx`
Expected: FAIL — current `aside` className has no `overflow-x-hidden`.

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/components/SourcePanel.tsx`:

1. Add `overflow-x-hidden` to the `aside`'s className (line 124), so the panel itself never
   scrolls horizontally as a block:

```tsx
      className="fixed inset-0 z-40 flex w-full flex-col overflow-x-hidden overflow-y-auto border-l border-slate-200 bg-slate-50 p-4 focus:outline-none md:static md:inset-auto md:z-auto md:w-80 md:shrink-0"
```

2. Add `break-words` to the metadata paragraph (line 147-152) and the markdown body wrapper
   (line 176), so any individual long token wraps within the panel width instead of being
   silently clipped by the new `overflow-x-hidden`:

```tsx
      <p className="mb-3 break-words text-xs text-slate-400">
```

```tsx
        <div className="break-words text-sm text-slate-700 [&_h1]:mb-1 ...">
```

(keep the rest of that long utility-class string unchanged — only prepend `break-words` to it)

3. Make the header row (Copy/Close controls) sticky so it always stays reachable regardless of
   how tall the content below is (line 126):

```tsx
      <div className="sticky top-0 z-10 mb-3 flex items-center justify-between bg-slate-50">
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/SourcePanel.test.tsx`
Expected: PASS, including all pre-existing tests in this file.

- [ ] **Step 5: Run the full frontend test suite, typecheck, lint, build**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npx oxlint . && npx vite build`
Expected: all PASS/clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SourcePanel.tsx frontend/src/components/SourcePanel.test.tsx
git commit -m "fix: contain SourcePanel horizontal overflow, keep controls reachable (ERP-085)"
```

---

### Task 5: Update tickets and close out

**Files:**
- Modify: `.ai/tickets/ERP-085.md`, `.ai/tickets/ERP-086.md`, `.ai/memory/current-state.md`

- [ ] **Step 1:** Mark ERP-085's remaining acceptance criterion (`Fix what's found`) checked,
  set `Status: Done`, and add a brief "Fixed" note describing what changed (Tasks 3-4 above).
- [ ] **Step 2:** Mark ERP-086's acceptance criteria checked (the redeploy one only after Task 2
  Step 3 is actually confirmed and run), set `Status: Done`, note the friendly-error-message fix
  and the memory bump.
- [ ] **Step 3:** Update `.ai/memory/current-state.md`'s relevant "Next Planned Work" bullets to
  reflect both tickets as Done, following this file's existing style (see how prior Done tickets
  are folded into "What Exists" bullets further up the file).
- [ ] **Step 4: Commit**

```bash
git add .ai/tickets/ERP-085.md .ai/tickets/ERP-086.md .ai/memory/current-state.md
git commit -m "docs: close out ERP-085 and ERP-086"
```
