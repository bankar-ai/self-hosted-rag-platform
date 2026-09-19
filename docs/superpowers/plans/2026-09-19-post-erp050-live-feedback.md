# Post-ERP-050 Live Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix eight issues surfaced by live-testing ERP-050: raw markdown/HTML in the source panel, missing copy affordances, a confirmed hallucinated date, an upload flow that starts immediately instead of staging, no bulk delete or delete confirmation, an orphaned-temp-file leak on dismissed failed uploads, silent upload-transfer failures, and a `Button` color-cascade bug causing invisible Delete/Retry/Dismiss text.

**Architecture:** Mostly independent frontend fixes across `SourcePanel.tsx`, `ChatPage.tsx`, and a substantial rework of `DocumentsPage.tsx`; one backend addition (`DELETE /ingestion/jobs/{job_id}`); one prompt-only backend change. `Button` gains a `variant` prop first since later Documents-page work depends on it rendering correctly.

**Tech Stack:** React + TypeScript + Tailwind (frontend, existing stack) + `react-markdown`/`rehype-raw`/`rehype-sanitize` (new, approved). FastAPI (backend, existing stack, no new dependency).

**Spec:** `docs/superpowers/specs/2026-09-19-post-erp050-live-feedback-design.md`

## Global Constraints

- No new backend dependency. Frontend: `react-markdown@10.1.0`, `rehype-raw@7.0.0`, `rehype-sanitize@6.0.0` are the only new dependencies, approved for `SourcePanel` only.
- `markdownLite.tsx` (the chat's own renderer) is NOT touched by this plan — only `SourcePanel` gets the new markdown library.
- Every new/changed backend endpoint is owner-scoped, returning `404` for "doesn't exist" and "exists but isn't yours" — matching this codebase's existing convention.
- `Button`'s default (`variant` omitted) must render pixel-identical to today for every existing call site — no visual regression on Send/New chat/Login/etc.
- Delete confirmation uses `window.confirm` (not a custom modal), matching this app's existing native-dialog pattern (rename already uses `window.prompt`).
- ERP-069 (prompt fix) has no automated test — verified via live reproduction only.

---

### Task 1: `Button` variant prop (ERP-074)

**Files:**
- Modify: `frontend/src/components/ui/button.tsx`
- Test: `frontend/src/components/ui/button.test.tsx` (new)

**Interfaces:**
- Produces: `Button` gains `variant?: "primary" | "outline" | "danger"` (default `"primary"`)

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/ui/button.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Button } from "./button";

describe("Button", () => {
  it("defaults to the primary variant (brand background, white text)", () => {
    render(<Button>Click me</Button>);
    const button = screen.getByRole("button", { name: "Click me" });
    expect(button.className).toContain("bg-brand");
    expect(button.className).toContain("text-white");
    expect(button.className).not.toContain("bg-white");
  });

  it("renders the outline variant with no background/text-color overlap with primary", () => {
    render(<Button variant="outline">Retry</Button>);
    const button = screen.getByRole("button", { name: "Retry" });
    expect(button.className).toContain("bg-white");
    expect(button.className).not.toContain("bg-brand");
    expect(button.className).not.toContain("text-white");
  });

  it("renders the danger variant with no background/text-color overlap with primary", () => {
    render(<Button variant="danger">Delete</Button>);
    const button = screen.getByRole("button", { name: "Delete" });
    expect(button.className).toContain("text-red-600");
    expect(button.className).not.toContain("bg-brand");
    expect(button.className).not.toContain("text-white");
  });

  it("still accepts a className for structural overrides alongside any variant", () => {
    render(<Button className="w-full">Full width</Button>);
    expect(screen.getByRole("button", { name: "Full width" }).className).toContain("w-full");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npm test -- button`
Expected: FAIL — `variant` prop doesn't exist yet, `bg-white`/`text-red-600` never appear

- [ ] **Step 3: Implement the variant prop**

Replace `frontend/src/components/ui/button.tsx`:

```tsx
import type { ButtonHTMLAttributes } from "react";

type ButtonVariant = "primary" | "outline" | "danger";

/**
 * Each variant is a complete, self-contained color class set with no background/text-color
 * utility shared across variants (ERP-074) -- Tailwind resolves conflicting utility classes by
 * generated-stylesheet order, not by the order they appear in a class string, so a per-call-site
 * override like `className="bg-white text-red-600"` layered on top of a hardcoded default
 * (e.g. `bg-brand text-white`) could silently lose that fight and render invisible text. Giving
 * each look its own variant removes the conflict entirely instead of patching around it.
 */
const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary: "bg-brand text-white hover:bg-brand-dark",
  outline: "bg-white text-slate-700 ring-1 ring-slate-300 hover:bg-slate-100",
  danger: "bg-white text-red-600 ring-1 ring-red-200 hover:bg-red-50",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

export function Button({ variant = "primary", className = "", ...props }: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center rounded-md px-4 py-2 text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 ${VARIANT_CLASSES[variant]} ${className}`}
      {...props}
    />
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- button`
Expected: 4 passed

- [ ] **Step 5: Type-check, lint, and run the full frontend suite**

Run: `npm run build && npm run lint && npm test`
Expected: all clean, all existing tests still passing (every bare `<Button>` call site keeps
today's exact look since `variant` defaults to `"primary"`, which is byte-for-byte the old
unconditional class string)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ui/button.tsx frontend/src/components/ui/button.test.tsx
git commit -m "fix(frontend): add Button variant prop to fix color-class cascade conflict (ERP-074)"
```

---

### Task 2: SourcePanel markdown/HTML rendering (ERP-067)

**Files:**
- Modify: `frontend/src/components/SourcePanel.tsx`
- Modify: `frontend/src/components/SourcePanel.test.tsx`
- Modify: `frontend/package.json` / `frontend/package-lock.json` (via `npm install`)

**Interfaces:**
- No change to `SourcePanelProps` or exported shape — this is a body-rendering change only.

- [ ] **Step 1: Install the new dependencies**

Run (from `frontend/`):

```bash
npm install react-markdown@10.1.0 rehype-raw@7.0.0 rehype-sanitize@6.0.0
```

- [ ] **Step 2: Write the failing tests**

Add to `frontend/src/components/SourcePanel.test.tsx`, inside the existing `describe("SourcePanel", ...)` block (after the existing tests):

```tsx
  it("renders markdown formatting instead of literal syntax", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            chunk_id: "doc1-0",
            document_id: "doc1",
            text: "# Roof Falls\n\n**81%** of deaths occur in construction. <mark>Highlighted</mark> and <u>underlined</u> text.",
            section_path: [],
            page_start: 1,
            page_end: 1,
            source_filename: "simple.pdf",
          }),
          { status: 200 }
        )
      )
    );

    render(<SourcePanel citation={citation} onClose={() => {}} />);

    await waitFor(() => expect(screen.getByText("Roof Falls")).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Roof Falls" }).tagName).toBe("H1");
    expect(screen.getByText("81%").tagName).toBe("STRONG");
    expect(screen.getByText("Highlighted").tagName).toBe("MARK");
    expect(screen.getByText("underlined").tagName).toBe("U");
    expect(screen.queryByText(/\*\*/)).toBeNull();
    expect(screen.queryByText(/<mark>/)).toBeNull();
  });

  it("sanitizes a disallowed tag instead of rendering it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            chunk_id: "doc1-0",
            document_id: "doc1",
            text: "Safe text <script>window.__pwned = true;</script> more text.",
            section_path: [],
            page_start: 1,
            page_end: 1,
            source_filename: "simple.pdf",
          }),
          { status: 200 }
        )
      )
    );

    render(<SourcePanel citation={citation} onClose={() => {}} />);

    await waitFor(() => expect(screen.getByText(/Safe text/)).toBeInTheDocument());
    expect(document.querySelector("script")).toBeNull();
  });
```

- [ ] **Step 3: Run tests to verify they fail**

Run (from `frontend/`): `npm test -- SourcePanel`
Expected: FAIL — the current implementation renders `state.text` as a plain string, so
`**81%**`/`<mark>` show as literal text, not real `<strong>`/`<mark>` elements

- [ ] **Step 4: Implement markdown rendering**

Replace `frontend/src/components/SourcePanel.tsx`'s body rendering. Add imports at the top:

```tsx
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import { apiFetch } from "../lib/apiClient";
import type { ChunkDetail, Citation } from "../lib/types";
```

Add, alongside the existing `PanelState` type (module scope, outside the component):

```tsx
// PDF parsing sometimes emits <mark>/<u> for highlighted/underlined text -- rehype-sanitize's
// default schema doesn't allow either tag, so both would otherwise be stripped along with any
// genuinely unsafe markup. Extending the allow-list, not replacing it, keeps everything else
// (script tags, event handlers, etc.) sanitized away as normal.
const sanitizeSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "mark", "u"],
};
```

Replace the `{state.status === "loaded" && ( ... )}` block (currently
`<p className="whitespace-pre-wrap text-sm text-slate-700">{state.text}</p>`) with:

```tsx
      {state.status === "loaded" && (
        <div className="text-sm text-slate-700 [&_h1]:mb-1 [&_h1]:mt-3 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mb-1 [&_h2]:mt-3 [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:mb-1 [&_h3]:mt-2 [&_h3]:text-sm [&_h3]:font-semibold [&_h4]:mb-1 [&_h4]:mt-2 [&_h4]:text-sm [&_h4]:font-semibold [&_h5]:mb-1 [&_h5]:mt-2 [&_h5]:text-sm [&_h5]:font-semibold [&_p]:mb-2 [&_ul]:mb-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:mb-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_mark]:bg-yellow-200 [&_mark]:px-0.5 [&_u]:underline">
          <ReactMarkdown rehypePlugins={[rehypeRaw, [rehypeSanitize, sanitizeSchema]]}>
            {state.text}
          </ReactMarkdown>
        </div>
      )}
```

- [ ] **Step 5: Run tests to verify they pass**

Run (from `frontend/`): `npm test -- SourcePanel`
Expected: 7 passed (5 existing + 2 new)

- [ ] **Step 6: Type-check, lint, and run the full frontend suite**

Run: `npm run build && npm run lint && npm test`
Expected: all clean, all tests passing

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/components/SourcePanel.tsx frontend/src/components/SourcePanel.test.tsx
git commit -m "fix(frontend): render SourcePanel content as real markdown/HTML, not literal syntax (ERP-067)"
```

---

### Task 3: Copy affordances (ERP-068)

**Files:**
- Create: `frontend/src/components/CopyButton.tsx`
- Create: `frontend/src/components/CopyButton.test.tsx`
- Modify: `frontend/src/pages/ChatPage.tsx`
- Modify: `frontend/src/components/SourcePanel.tsx`

**Interfaces:**
- Produces: `export default function CopyButton({ getText, label, className }: { getText: () => string; label?: string; className?: string })`
- Consumes (by `ChatPage.tsx`/`SourcePanel.tsx`): the above

- [ ] **Step 1: Write the failing test for `CopyButton`**

Create `frontend/src/components/CopyButton.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import CopyButton from "./CopyButton";

describe("CopyButton", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("copies the text returned by getText and shows a brief confirmation", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<CopyButton getText={() => "hello world"} label="Copy" />);

    await userEvent.click(screen.getByRole("button", { name: "Copy" }));

    expect(writeText).toHaveBeenCalledWith("hello world");
    await waitFor(() => expect(screen.getByRole("button", { name: "Copied!" })).toBeInTheDocument());
  });

  it("calls getText lazily, at click time, not at render time", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    let currentText = "first";
    const getText = vi.fn(() => currentText);

    render(<CopyButton getText={getText} label="Copy" />);
    currentText = "second";
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));

    expect(writeText).toHaveBeenCalledWith("second");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npm test -- CopyButton`
Expected: FAIL — `CopyButton` module doesn't exist yet

- [ ] **Step 3: Implement `CopyButton`**

Create `frontend/src/components/CopyButton.tsx`:

```tsx
import { useState } from "react";

interface CopyButtonProps {
  getText: () => string;
  label?: string;
  className?: string;
}

/** A small reusable copy-to-clipboard button (ERP-068), used for a single answer, a whole
 * conversation, and a source panel's chunk text -- `getText` is called at click time (not
 * render time) so the copied content always reflects the latest state. */
export default function CopyButton({ getText, label = "Copy", className = "" }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  async function handleClick(): Promise<void> {
    try {
      await navigator.clipboard.writeText(getText());
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // best-effort -- clipboard access can be denied by the browser; no user-facing error
    }
  }

  return (
    <button type="button" className={className} onClick={() => void handleClick()}>
      {copied ? "Copied!" : label}
    </button>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npm test -- CopyButton`
Expected: 2 passed

- [ ] **Step 5: Wire into `SourcePanel.tsx`**

Add the import: `import CopyButton from "./CopyButton";`

Replace the header `<div className="mb-3 flex items-center justify-between">...</div>` block
with:

```tsx
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Source</p>
        <div className="flex items-center gap-1">
          {state.status === "loaded" && (
            <CopyButton
              getText={() => state.text}
              label="Copy"
              className="rounded-md px-1.5 py-0.5 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-700"
            />
          )}
          <button
            className="rounded-md px-1.5 py-0.5 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-700"
            onClick={onClose}
            aria-label="Close source panel"
          >
            ✕
          </button>
        </div>
      </div>
```

- [ ] **Step 6: Add a test for the source-panel copy button**

Add to `frontend/src/components/SourcePanel.test.tsx`:

```tsx
  it("copies the loaded chunk's text via the copy button", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            chunk_id: "doc1-0",
            document_id: "doc1",
            text: "Copy this text.",
            section_path: [],
            page_start: 1,
            page_end: 1,
            source_filename: "simple.pdf",
          }),
          { status: 200 }
        )
      )
    );

    render(<SourcePanel citation={citation} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("Copy this text.")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("Copy this text.");
  });
```

- [ ] **Step 7: Wire into `ChatPage.tsx` — per-message copy**

Add the import: `import CopyButton from "../components/CopyButton";`

Replace the feedback-row block (currently
`{message.role === "assistant" && message.id && !isPendingAssistant && ( ... )}`) with:

```tsx
                  {message.role === "assistant" && !isPendingAssistant && message.content && (
                    <div className="mt-2 flex items-center gap-1 border-t border-slate-200 pt-2">
                      <CopyButton
                        getText={() => message.content}
                        label="Copy"
                        className="rounded px-1.5 py-0.5 text-xs text-slate-400 hover:bg-slate-200"
                      />
                      {message.id && (
                        <>
                          <button
                            className={`rounded px-1.5 py-0.5 text-xs ${
                              message.feedback === "up"
                                ? "bg-emerald-100 text-emerald-700"
                                : "text-slate-400 hover:bg-slate-200"
                            }`}
                            title="Good answer"
                            onClick={() => void setMessageFeedback(message.id!, "up")}
                          >
                            👍
                          </button>
                          <button
                            className={`rounded px-1.5 py-0.5 text-xs ${
                              message.feedback === "down"
                                ? "bg-red-100 text-red-700"
                                : "text-slate-400 hover:bg-slate-200"
                            }`}
                            title="Bad answer"
                            onClick={() => void setMessageFeedback(message.id!, "down")}
                          >
                            👎
                          </button>
                        </>
                      )}
                    </div>
                  )}
```

- [ ] **Step 8: Wire into `ChatPage.tsx` — per-conversation copy**

Add this helper function near `formatCitation` (module scope):

```tsx
function buildTranscriptText(messages: { role: string; content: string }[]): string {
  return messages
    .filter((m) => m.role !== "error")
    .map((m) => `${m.role === "user" ? "You" : "Assistant"}: ${m.content}`)
    .join("\n\n");
}
```

In the JSX, immediately before `<div className="mx-auto flex max-w-2xl flex-col gap-3">`
(inside `<div className="flex-1 overflow-y-auto px-6 py-4">`), add:

```tsx
          {messages.length > 0 && (
            <div className="mx-auto mb-2 flex max-w-2xl justify-end">
              <CopyButton
                getText={() => buildTranscriptText(messages)}
                label="Copy conversation"
                className="rounded-md px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 hover:text-slate-700"
              />
            </div>
          )}
```

- [ ] **Step 9: Type-check, lint, and run the full frontend suite**

Run: `npm run build && npm run lint && npm test`
Expected: all clean, all tests passing (no existing `ChatPage.tsx` test file to extend — this
task's `ChatPage.tsx` changes are verified via the underlying `CopyButton`'s own tests plus
build/lint, consistent with how ERP-050 handled `ChatPage.tsx` wiring)

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/CopyButton.tsx frontend/src/components/CopyButton.test.tsx frontend/src/components/SourcePanel.tsx frontend/src/components/SourcePanel.test.tsx frontend/src/pages/ChatPage.tsx
git commit -m "feat(frontend): add copy-to-clipboard for messages, conversations, and source text (ERP-068)"
```

---

### Task 4: Anti-hallucination prompt fix (ERP-069)

**Files:**
- Modify: `app/generation/prompt.py`

**Interfaces:**
- No signature change — `SYSTEM_PROMPT`'s text content only.

- [ ] **Step 1: Update `SYSTEM_PROMPT`**

In `app/generation/prompt.py`, replace this sentence inside `SYSTEM_PROMPT`:

```python
    "If the context does not contain enough information to answer, say so explicitly "
    "-- do not use outside knowledge, and never guess, assume, or state something not "
    "directly supported by the provided context. Any bracketed markers appearing in the "
```

with:

```python
    "If the context does not contain enough information to answer, say so explicitly "
    "-- do not use outside knowledge, and never guess, assume, or state something not "
    "directly supported by the provided context. Never state a specific date, number, name, "
    "or other fact unless it is verbatim present in the provided context -- if a question "
    "asks for a detail the context does not specify, say the context does not specify it "
    "rather than inferring, estimating, or fabricating one. Any bracketed markers appearing "
    "in the "
```

(Note: this only inserts the new sentence and reflows the following text; every other line of
`SYSTEM_PROMPT` is unchanged.)

- [ ] **Step 2: Run the existing generation test suite**

Run: `uv run pytest tests/generation/ -q`
Expected: all still passing (no test asserts `SYSTEM_PROMPT`'s literal text, so this change
doesn't need new automated tests — see the plan's Global Constraints)

- [ ] **Step 3: Lint and type-check**

Run: `uv run ruff check app/generation/prompt.py && uv run mypy app/generation/prompt.py`
Expected: both clean

- [ ] **Step 4: Commit**

```bash
git add app/generation/prompt.py
git commit -m "fix(generation): forbid inventing dates/numbers/facts not in context (ERP-069)"
```

- [ ] **Step 5: Live-reproduce the fix (manual verification, not part of the automated suite)**

This step is executed by the controller after all tasks are implemented and deployed, not by
this task's implementer — record it in the ledger as a note to revisit during live
verification: re-ingest the exact "Safety Guidelines.pdf" (NIOSH FACE fall-prevention fact
sheet) used in the live-testing conversation, ask "explain this July 21, 2026 is the date of
one of the recorded deaths" as a follow-up to a question about ladder deaths, and confirm the
answer no longer states a date not present in the source (e.g., it should say the context
doesn't specify a date, rather than asserting "July 21, 2026").

---

### Task 5: Backend — `DELETE /ingestion/jobs/{job_id}` (ERP-072, backend half)

**Files:**
- Modify: `app/ingestion/jobs.py`
- Modify: `app/ingestion/router.py`
- Test: `tests/ingestion/test_jobs.py`
- Test: `tests/ingestion/test_router.py`

**Interfaces:**
- Produces: `delete_job(job_id: str, owner_id: uuid.UUID) -> bool` (`app/ingestion/jobs.py`),
  `DELETE /ingestion/jobs/{job_id}` endpoint

- [ ] **Step 1: Write the failing tests in `test_jobs.py`**

Add to `tests/ingestion/test_jobs.py`'s import line:

```python
from app.ingestion.jobs import create_job, delete_job, get_job, retry_job, run_ingestion_job
```

Append at the end of the file:

```python
def test_delete_job_removes_the_job_and_unlinks_its_temp_file(tmp_path):
    pdf_path = tmp_path / "leftover.pdf"
    pdf_path.write_bytes(b"not a real pdf")
    job_id = create_job(_TEST_OWNER_ID, str(pdf_path), "leftover.pdf")
    run_ingestion_job(job_id, str(pdf_path), "leftover.pdf", _settings(), _TEST_OWNER_ID)
    assert get_job(job_id).status == JobStatus.FAILED
    assert pdf_path.exists()

    result = delete_job(job_id, _TEST_OWNER_ID)

    assert result is True
    assert get_job(job_id) is None
    assert not pdf_path.exists()


def test_delete_job_returns_false_for_unknown_job():
    assert delete_job("does-not-exist", _TEST_OWNER_ID) is False


def test_delete_job_returns_false_for_a_job_that_is_not_failed():
    job_id = create_job(_TEST_OWNER_ID, "/tmp/unused.pdf", "unused.pdf")
    assert delete_job(job_id, _TEST_OWNER_ID) is False


def test_delete_job_returns_false_for_wrong_owner(tmp_path):
    pdf_path = tmp_path / "leftover2.pdf"
    pdf_path.write_bytes(b"not a real pdf")
    job_id = create_job(_TEST_OWNER_ID, str(pdf_path), "leftover2.pdf")
    run_ingestion_job(job_id, str(pdf_path), "leftover2.pdf", _settings(), _TEST_OWNER_ID)

    assert delete_job(job_id, uuid.uuid4()) is False
    assert get_job(job_id).status == JobStatus.FAILED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingestion/test_jobs.py -k delete_job -v`
Expected: FAIL with `ImportError: cannot import name 'delete_job'`

- [ ] **Step 3: Implement `delete_job`**

Append to `app/ingestion/jobs.py` (after `run_ingestion_job`):

```python


def delete_job(job_id: str, owner_id: uuid.UUID) -> bool:
    """Delete a FAILED job's record and its retained temp file (ERP-072).

    A failed job's uploaded file is deliberately kept on disk so `retry_job` can reuse it
    without a re-upload -- but if the caller instead dismisses the job for good, nothing
    previously cleaned up either the in-memory record or that file, leaking disk space on a
    resource-constrained deployment. Returns `False` (mapped to `404` by the router) if the
    job doesn't exist, isn't owned by `owner_id`, or isn't currently `FAILED` -- mirroring
    `retry_job`'s exact guard, since dismissing only makes sense for the same states retrying
    would apply to.
    """
    with _lock:
        record = _jobs.get(job_id)
        if record is None or record.owner_id != owner_id or record.status != JobStatus.FAILED:
            return False
        pdf_path = record.pdf_path
        del _jobs[job_id]

    Path(pdf_path).unlink(missing_ok=True)
    try:
        Path(pdf_path).parent.rmdir()
    except OSError:
        pass
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingestion/test_jobs.py -k delete_job -v`
Expected: 4 passed

- [ ] **Step 5: Write the failing router tests**

Append to `tests/ingestion/test_router.py`:

```python
def test_delete_job_removes_a_failed_job(monkeypatch, simple_text_pdf, auth_headers):
    import app.ingestion.jobs as jobs_module

    monkeypatch.setattr(
        jobs_module, "ingest_pdf", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    upload = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    job_id = upload.json()["job_id"]
    failed = _poll_until_done(job_id, auth_headers)
    assert failed["status"] == "failed"

    response = client.delete(f"/ingestion/jobs/{job_id}", headers=auth_headers)
    assert response.status_code == 204

    status_response = client.get(f"/ingestion/jobs/{job_id}", headers=auth_headers)
    assert status_response.status_code == 404


def test_delete_job_404_for_unknown_job(auth_headers):
    response = client.delete("/ingestion/jobs/does-not-exist", headers=auth_headers)
    assert response.status_code == 404


def test_delete_job_404_for_a_job_that_is_not_failed(simple_text_pdf, auth_headers):
    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    upload = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    job_id = upload.json()["job_id"]
    done = _poll_until_done(job_id, auth_headers)
    assert done["status"] == "done"

    response = client.delete(f"/ingestion/jobs/{job_id}", headers=auth_headers)
    assert response.status_code == 404


def test_delete_job_404_for_job_belonging_to_another_user(monkeypatch, simple_text_pdf, auth_headers):
    import app.ingestion.jobs as jobs_module

    monkeypatch.setattr(
        jobs_module, "ingest_pdf", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    upload = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    job_id = upload.json()["job_id"]
    _poll_until_done(job_id, auth_headers)

    other_user_headers = register_and_login(client, "ingestion-delete-job-other-owner")
    response = client.delete(f"/ingestion/jobs/{job_id}", headers=other_user_headers)
    assert response.status_code == 404
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/ingestion/test_router.py -k test_delete_job -v`
Expected: FAIL — the route doesn't exist yet (404 from FastAPI's own routing, not from the
endpoint's own logic, so `test_delete_job_removes_a_failed_job`'s `204` assertion fails)

- [ ] **Step 7: Implement the endpoint**

In `app/ingestion/router.py`, add `status` import if not already present (it already is, per
the existing `status.HTTP_202_ACCEPTED` usage). Append this endpoint after `retry_job`:

```python


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job_endpoint(job_id: str, current_user: CurrentUser = Depends(get_current_user)) -> None:
    """Delete a failed job's record and temp file. 404 if unknown, not owned, or not failed."""
    if not jobs.delete_job(job_id, current_user.id):
        raise HTTPException(status_code=404, detail="Failed job not found")
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/ingestion/test_router.py -k test_delete_job -v`
Expected: 4 passed

- [ ] **Step 9: Run the full backend suite, lint, and type-check**

Run: `uv run ruff check . && uv run mypy app/ && uv run pytest -q`
Expected: all clean, all tests passing (was 508 before this task)

- [ ] **Step 10: Commit**

```bash
git add app/ingestion/jobs.py app/ingestion/router.py tests/ingestion/test_jobs.py tests/ingestion/test_router.py
git commit -m "feat(ingestion): add DELETE /ingestion/jobs/{id} to clean up dismissed failed uploads (ERP-072)"
```

---

### Task 6: DocumentsPage — staged upload + upload-transfer failure UI (ERP-070, ERP-073)

**Files:**
- Modify: `frontend/src/pages/DocumentsPage.tsx`
- Test: `frontend/src/pages/DocumentsPage.test.tsx` (new)

**Interfaces:**
- Consumes: `Button` with `variant` (Task 1), `apiFetch`/`uploadWithProgress` (existing,
  unchanged signatures)
- Produces: nothing new consumed by later tasks except the reworked file itself, which Task 7
  edits further

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/pages/DocumentsPage.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as apiClient from "../lib/apiClient";
import { AuthProvider } from "../lib/AuthContext";
import { setTokens } from "../lib/tokenStorage";
import DocumentsPage from "./DocumentsPage";

function stubAuthAndEmptyDocuments(extra?: (url: string, init?: RequestInit) => Response | null) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if (url.includes("/auth/me")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ id: "u1", email: "u@example.com", role: "user", is_active: true }),
            { status: 200 }
          )
        );
      }
      const extraResponse = extra?.(url, init);
      if (extraResponse) return Promise.resolve(extraResponse);
      if (url.endsWith("/documents")) {
        return Promise.resolve(new Response(JSON.stringify({ documents: [] }), { status: 200 }));
      }
      return Promise.resolve(new Response("{}", { status: 200 }));
    })
  );
}

describe("DocumentsPage", () => {
  beforeEach(() => {
    localStorage.clear();
    setTokens({ accessToken: "a", refreshToken: "b" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("stages a selected file without uploading it until Upload is clicked", async () => {
    stubAuthAndEmptyDocuments();
    const uploadSpy = vi
      .spyOn(apiClient, "uploadWithProgress")
      .mockResolvedValue({ ok: true, status: 202, body: { job_id: "job-1" } });

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText(/no documents uploaded yet/i));

    const file = new File(["%PDF-1.4"], "test.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText(/choose pdf files/i) as HTMLInputElement;
    await userEvent.upload(input, file);

    expect(screen.getByText("test.pdf")).toBeInTheDocument();
    expect(uploadSpy).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /upload 1 file/i }));

    await waitFor(() => expect(uploadSpy).toHaveBeenCalledTimes(1));
  });

  it("removes a staged file when its remove button is clicked", async () => {
    stubAuthAndEmptyDocuments();

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText(/no documents uploaded yet/i));

    const file = new File(["%PDF-1.4"], "remove-me.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText(/choose pdf files/i) as HTMLInputElement;
    await userEvent.upload(input, file);
    expect(screen.getByText("remove-me.pdf")).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText(/remove remove-me\.pdf/i));

    expect(screen.queryByText("remove-me.pdf")).not.toBeInTheDocument();
  });

  it("shows a failed state and lets the user retry when the upload transfer itself fails", async () => {
    stubAuthAndEmptyDocuments((url) => {
      if (url.includes("/ingestion/jobs/job-y")) {
        return new Response(
          JSON.stringify({ status: "done", result: { document_id: "d1", chunks: [] } }),
          { status: 200 }
        );
      }
      return null;
    });
    const uploadSpy = vi
      .spyOn(apiClient, "uploadWithProgress")
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce({ ok: true, status: 202, body: { job_id: "job-y" } });

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText(/no documents uploaded yet/i));

    const file = new File(["%PDF-1.4"], "flaky.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText(/choose pdf files/i) as HTMLInputElement;
    await userEvent.upload(input, file);
    await userEvent.click(screen.getByRole("button", { name: /upload 1 file/i }));

    await waitFor(() => expect(screen.getByText(/upload failed/i)).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(uploadSpy).toHaveBeenCalledTimes(2));
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npm test -- DocumentsPage`
Expected: FAIL — no staging step exists yet (files upload immediately), so the "not called
until Upload is clicked" assertion fails; there's no per-file remove button; there's no failed
upload state or "Try again" button

- [ ] **Step 3: Implement staged upload and failure UI**

Replace `frontend/src/pages/DocumentsPage.tsx` in full:

```tsx
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { apiFetch, uploadWithProgress } from "../lib/apiClient";
import { useAuth } from "../lib/AuthContext";
import { getDocumentsStore, type RecentDocument } from "../lib/documentsStore";
import type { DocumentListResponse, DocumentSummary, JobStatusResponse } from "../lib/types";

const POLL_INTERVAL_MS = 2000;

// Must match the live `INGESTION_MAX_UPLOAD_SIZE_BYTES` backend setting (ERP-051) -- there is
// no settings-introspection endpoint, so this is a matching constant, not a fetched value.
const MAX_UPLOAD_SIZE_BYTES = 20_000_000;
const MAX_UPLOAD_SIZE_LABEL = "20 MB";

const IN_PROGRESS_STATUS_STYLES: Record<"pending" | "processing" | "failed", string> = {
  pending: "bg-amber-100 text-amber-700",
  processing: "bg-amber-100 text-amber-700",
  failed: "bg-red-100 text-red-700",
};

interface UploadInFlight {
  id: string;
  file: File;
  progress: number;
  status: "uploading" | "failed";
}

export default function DocumentsPage() {
  const { userId } = useAuth();
  // In-flight uploads only (pending/processing/failed) -- still tracked client-side, since a
  // job that hasn't finished (or never will) has no backend row to read back. A job that
  // reaches "done" is removed from here as soon as it's confirmed in `serverDocuments`.
  const [inProgress, setInProgress] = useState<RecentDocument[]>(() =>
    userId ? getDocumentsStore(userId).list().filter((doc) => doc.status !== "done") : []
  );
  const [serverDocuments, setServerDocuments] = useState<DocumentSummary[]>([]);
  // Files chosen or dropped but not yet uploaded (ERP-070) -- upload only starts once the
  // user explicitly clicks "Upload", not on selection/drop.
  const [stagedFiles, setStagedFiles] = useState<File[]>([]);
  // Byte-transfer progress/failure, for files currently being sent -- separate from
  // `inProgress`, which starts only once the backend has accepted the file and created a job.
  const [uploadsInFlight, setUploadsInFlight] = useState<UploadInFlight[]>([]);
  const [rejectedFiles, setRejectedFiles] = useState<string[]>([]);
  const [deletingIds, setDeletingIds] = useState<Set<string>>(new Set());
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<Set<string>>(new Set());
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function refreshServerDocuments(): Promise<void> {
    const response = await apiFetch("/documents");
    if (!response.ok) return;
    const body = (await response.json()) as DocumentListResponse;
    setServerDocuments(body.documents);
  }

  useEffect(() => {
    void refreshServerDocuments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // AuthGuard already guarantees a valid token (and therefore a decodable userId) before this
  // page can render at all -- this is a type-safety formality, not a loading state to wait out.
  if (!userId) {
    return null;
  }
  const scopedUserId = userId;

  function updateInProgress(doc: RecentDocument): void {
    const store = getDocumentsStore(scopedUserId);
    store.upsert(doc);
    setInProgress(store.list().filter((d) => d.status !== "done"));
  }

  async function dismissInProgress(jobId: string): Promise<void> {
    await apiFetch(`/ingestion/jobs/${jobId}`, { method: "DELETE" }).catch(() => null);
    const store = getDocumentsStore(scopedUserId);
    store.remove(jobId);
    setInProgress(store.list().filter((d) => d.status !== "done"));
  }

  async function pollJob(jobId: string, filename: string): Promise<void> {
    const response = await apiFetch(`/ingestion/jobs/${jobId}`);
    const job = (await response.json()) as JobStatusResponse;

    if (job.status === "pending" || job.status === "processing") {
      updateInProgress({ id: jobId, title: filename, lastUpdated: Date.now(), status: job.status });
      setTimeout(() => void pollJob(jobId, filename), POLL_INTERVAL_MS);
      return;
    }

    if (job.status === "failed") {
      updateInProgress({
        id: jobId,
        title: filename,
        lastUpdated: Date.now(),
        status: "failed",
        error: job.error ?? undefined,
      });
      return;
    }

    // Done: the document now has its own row on the backend, so it no longer needs to be
    // tracked as a local in-flight job -- drop it here and let `serverDocuments` show it.
    getDocumentsStore(scopedUserId).remove(jobId);
    setInProgress(getDocumentsStore(scopedUserId).list().filter((d) => d.status !== "done"));
    await refreshServerDocuments();
  }

  async function uploadOne(file: File, existingId?: string): Promise<void> {
    const uploadId = existingId ?? crypto.randomUUID();
    setUploadsInFlight((prev) => [
      ...prev.filter((u) => u.id !== uploadId),
      { id: uploadId, file, progress: 0, status: "uploading" },
    ]);

    const result = await uploadWithProgress("/ingestion/pdf", file, (fraction) => {
      setUploadsInFlight((prev) =>
        prev.map((u) => (u.id === uploadId ? { ...u, progress: fraction } : u))
      );
    }).catch(() => null);

    if (!result || !result.ok) {
      setUploadsInFlight((prev) =>
        prev.map((u) => (u.id === uploadId ? { ...u, status: "failed" } : u))
      );
      return;
    }

    setUploadsInFlight((prev) => prev.filter((u) => u.id !== uploadId));
    const { job_id: jobId } = result.body as { job_id: string };
    updateInProgress({ id: jobId, title: file.name, lastUpdated: Date.now(), status: "pending" });
    void pollJob(jobId, file.name);
  }

  function retryUpload(uploadId: string): void {
    const upload = uploadsInFlight.find((u) => u.id === uploadId);
    if (!upload) return;
    void uploadOne(upload.file, uploadId);
  }

  async function handleFiles(files: File[]): Promise<void> {
    await Promise.all(files.map((file) => uploadOne(file)));
  }

  function stageFiles(files: File[]): void {
    const accepted: File[] = [];
    const rejected: string[] = [];
    for (const file of files) {
      if (file.size > MAX_UPLOAD_SIZE_BYTES) {
        rejected.push(file.name);
      } else {
        accepted.push(file);
      }
    }
    setRejectedFiles(rejected);
    setStagedFiles((prev) => [...prev, ...accepted]);
  }

  function removeStagedFile(index: number): void {
    setStagedFiles((prev) => prev.filter((_, i) => i !== index));
  }

  async function startStagedUpload(): Promise<void> {
    const files = stagedFiles;
    setStagedFiles([]);
    await handleFiles(files);
  }

  function handleFileInputChange(): void {
    const files = fileInputRef.current?.files;
    if (!files || files.length === 0) return;
    stageFiles(Array.from(files));
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleDrop(event: React.DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    setIsDragging(false);
    const files = Array.from(event.dataTransfer.files).filter(
      (file) => file.type === "application/pdf"
    );
    if (files.length > 0) stageFiles(files);
  }

  async function deleteDocuments(documentIds: string[]): Promise<void> {
    const names = serverDocuments
      .filter((doc) => documentIds.includes(doc.document_id))
      .map((doc) => doc.filename);
    const confirmed = window.confirm(
      names.length === 1
        ? `This will permanently delete "${names[0]}". This cannot be undone. Continue?`
        : `This will permanently delete ${names.length} files (${names.join(", ")}). This cannot be undone. Continue?`
    );
    if (!confirmed) return;

    setDeletingIds((prev) => new Set([...prev, ...documentIds]));
    await Promise.all(documentIds.map((id) => apiFetch(`/documents/${id}`, { method: "DELETE" })));
    setServerDocuments((prev) => prev.filter((doc) => !documentIds.includes(doc.document_id)));
    setSelectedDocumentIds((prev) => {
      const next = new Set(prev);
      documentIds.forEach((id) => next.delete(id));
      return next;
    });
    setDeletingIds((prev) => {
      const next = new Set(prev);
      documentIds.forEach((id) => next.delete(id));
      return next;
    });
  }

  function toggleDocumentSelection(documentId: string): void {
    setSelectedDocumentIds((prev) => {
      const next = new Set(prev);
      if (next.has(documentId)) {
        next.delete(documentId);
      } else {
        next.add(documentId);
      }
      return next;
    });
  }

  function toggleSelectAll(): void {
    setSelectedDocumentIds((prev) =>
      prev.size === serverDocuments.length
        ? new Set()
        : new Set(serverDocuments.map((d) => d.document_id))
    );
  }

  async function handleRetry(jobId: string, filename: string): Promise<void> {
    setRetryingId(jobId);
    const response = await apiFetch(`/ingestion/jobs/${jobId}/retry`, { method: "POST" });
    setRetryingId(null);
    if (!response.ok) return;

    const { job_id: newJobId } = (await response.json()) as { job_id: string };
    await dismissInProgress(jobId);
    updateInProgress({ id: newJobId, title: filename, lastUpdated: Date.now(), status: "pending" });
    void pollJob(newJobId, filename);
  }

  return (
    <div className="mx-auto max-w-2xl p-8">
      <h1 className="mb-1 text-2xl font-semibold text-slate-900">Documents</h1>
      <p className="mb-6 text-sm text-slate-500">
        Upload one or more PDFs to make them searchable in Chat. Ingestion runs in the
        background.
      </p>
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        className={`mb-2 flex flex-col items-center gap-3 rounded-xl border border-dashed p-6 text-center transition-colors ${
          isDragging ? "border-slate-500 bg-slate-100" : "border-slate-300 bg-slate-50"
        }`}
      >
        <p className="text-sm text-slate-600">Drag and drop PDFs here, or</p>
        <div className="flex items-center gap-3">
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf"
            multiple
            aria-label="Choose PDF files"
            onChange={handleFileInputChange}
            className="text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-brand file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-white"
          />
        </div>
      </div>
      <p className="mb-2 text-xs text-slate-400">Maximum file size: {MAX_UPLOAD_SIZE_LABEL} per PDF.</p>

      {rejectedFiles.length > 0 && (
        <p className="mb-6 text-sm text-red-600">
          Too large (max {MAX_UPLOAD_SIZE_LABEL}), not uploaded: {rejectedFiles.join(", ")}
        </p>
      )}

      {stagedFiles.length > 0 && (
        <div className="mb-6 rounded-xl border border-slate-200 p-4">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
            Ready to upload
          </p>
          <ul className="mb-3 flex flex-col gap-1">
            {stagedFiles.map((file, index) => (
              <li
                key={`${file.name}-${index}`}
                className="flex items-center justify-between text-sm text-slate-700"
              >
                <span className="truncate">{file.name}</span>
                <button
                  type="button"
                  className="ml-2 shrink-0 text-xs text-slate-400 hover:text-slate-700"
                  onClick={() => removeStagedFile(index)}
                  aria-label={`Remove ${file.name} from upload`}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
          <Button onClick={() => void startStagedUpload()}>
            Upload {stagedFiles.length} file{stagedFiles.length === 1 ? "" : "s"}
          </Button>
        </div>
      )}

      {uploadsInFlight.length > 0 && (
        <ul className="mb-8 flex flex-col gap-2">
          {uploadsInFlight.map((upload) => (
            <li key={upload.id} className="rounded-xl border border-slate-200 p-4">
              <p className="mb-2 truncate text-sm font-medium text-slate-900">{upload.file.name}</p>
              {upload.status === "failed" ? (
                <div className="flex items-center justify-between">
                  <p className="text-sm text-red-600">Upload failed.</p>
                  <Button variant="outline" onClick={() => retryUpload(upload.id)}>
                    Try again
                  </Button>
                </div>
              ) : (
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
                  <div
                    className="h-full rounded-full bg-slate-900 transition-all"
                    style={{ width: `${Math.round(upload.progress * 100)}%` }}
                  />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {inProgress.length > 0 && (
        <>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
            In-progress uploads
          </p>
          <ul className="mb-8 flex flex-col gap-2">
            {inProgress.map((doc) => (
              <li
                key={doc.id}
                className="flex items-center justify-between rounded-xl border border-slate-200 p-4"
              >
                <div>
                  <p className="font-medium text-slate-900">{doc.title}</p>
                  {doc.error && <p className="mt-1 text-sm text-red-600">{doc.error}</p>}
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded-full px-3 py-1 text-xs font-medium ${IN_PROGRESS_STATUS_STYLES[doc.status as "pending" | "processing" | "failed"]}`}
                  >
                    {doc.status}
                  </span>
                  {doc.status === "failed" && (
                    <>
                      <Button
                        variant="outline"
                        onClick={() => void handleRetry(doc.id, doc.title)}
                        disabled={retryingId === doc.id}
                      >
                        {retryingId === doc.id ? "Retrying..." : "Retry"}
                      </Button>
                      <Button variant="outline" onClick={() => void dismissInProgress(doc.id)}>
                        Dismiss
                      </Button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
          Your documents
        </p>
        {selectedDocumentIds.size > 0 && (
          <Button
            variant="danger"
            onClick={() => void deleteDocuments(Array.from(selectedDocumentIds))}
          >
            Delete selected ({selectedDocumentIds.size})
          </Button>
        )}
      </div>
      {serverDocuments.length === 0 ? (
        <p className="text-sm text-slate-400">No documents uploaded yet.</p>
      ) : (
        <>
          <label className="mb-2 flex items-center gap-2 text-xs text-slate-500">
            <input
              type="checkbox"
              checked={selectedDocumentIds.size === serverDocuments.length}
              onChange={toggleSelectAll}
            />
            Select all
          </label>
          <ul className="flex flex-col gap-2">
            {serverDocuments.map((doc) => (
              <li
                key={doc.document_id}
                className="flex items-center justify-between rounded-xl border border-slate-200 p-4"
              >
                <label className="flex min-w-0 items-center gap-3">
                  <input
                    type="checkbox"
                    checked={selectedDocumentIds.has(doc.document_id)}
                    onChange={() => toggleDocumentSelection(doc.document_id)}
                  />
                  <span className="truncate font-medium text-slate-900">{doc.filename}</span>
                </label>
                <Button
                  variant="danger"
                  onClick={() => void deleteDocuments([doc.document_id])}
                  disabled={deletingIds.has(doc.document_id)}
                >
                  {deletingIds.has(doc.document_id) ? "Deleting..." : "Delete"}
                </Button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
```

(This single replacement also implements Task 7's ERP-071/ERP-072-frontend scope, since the
bulk-delete/confirmation/dismiss-wiring code is small enough to write once rather than as a
separate diff on top of a diff — Task 7 below only adds that task's tests against this same
code.)

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npm test -- DocumentsPage`
Expected: 3 passed

- [ ] **Step 5: Type-check, lint, and run the full frontend suite**

Run: `npm run build && npm run lint && npm test`
Expected: all clean, all tests passing

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/DocumentsPage.tsx frontend/src/pages/DocumentsPage.test.tsx
git commit -m "feat(frontend): stage uploads behind an explicit Upload button, show and allow retry for transfer failures (ERP-070, ERP-073)"
```

---

### Task 7: DocumentsPage — bulk delete, confirmation, and dismiss wiring tests (ERP-071, ERP-072 frontend half)

**Files:**
- Test: `frontend/src/pages/DocumentsPage.test.tsx`

**Interfaces:**
- Consumes: the `deleteDocuments`, `toggleDocumentSelection`, `toggleSelectAll`, and
  `dismissInProgress` behavior already implemented in Task 6's full-file rewrite (this task
  adds the tests confirming that behavior, since Task 6 wrote the implementation for both
  tickets at once).

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/pages/DocumentsPage.test.tsx`, inside the existing
`describe("DocumentsPage", ...)` block:

```tsx
  it("shows a confirmation before deleting, and does nothing if declined", async () => {
    stubAuthAndEmptyDocuments((url) => {
      if (url.endsWith("/documents")) {
        return new Response(
          JSON.stringify({
            documents: [{ document_id: "d1", filename: "existing.pdf", created_at: "2026-01-01T00:00:00Z" }],
          }),
          { status: 200 }
        );
      }
      return null;
    });
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText("existing.pdf"));

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(window.confirm).toHaveBeenCalled();
    expect(screen.getByText("existing.pdf")).toBeInTheDocument();
  });

  it("deletes a document when the confirmation is accepted", async () => {
    const deleteCalls: string[] = [];
    stubAuthAndEmptyDocuments((url, init) => {
      if (url.includes("/documents/") && init?.method === "DELETE") {
        deleteCalls.push(url);
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/documents")) {
        return new Response(
          JSON.stringify({
            documents: [{ document_id: "d1", filename: "existing.pdf", created_at: "2026-01-01T00:00:00Z" }],
          }),
          { status: 200 }
        );
      }
      return null;
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText("existing.pdf"));

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteCalls).toHaveLength(1));
    await waitFor(() => expect(screen.queryByText("existing.pdf")).not.toBeInTheDocument());
  });

  it("deletes multiple selected documents via bulk delete", async () => {
    const deleteCalls: string[] = [];
    stubAuthAndEmptyDocuments((url, init) => {
      if (url.includes("/documents/") && init?.method === "DELETE") {
        deleteCalls.push(url);
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/documents")) {
        return new Response(
          JSON.stringify({
            documents: [
              { document_id: "d1", filename: "one.pdf", created_at: "2026-01-01T00:00:00Z" },
              { document_id: "d2", filename: "two.pdf", created_at: "2026-01-01T00:00:00Z" },
            ],
          }),
          { status: 200 }
        );
      }
      return null;
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText("one.pdf"));

    await userEvent.click(screen.getByLabelText(/select all/i));
    await userEvent.click(screen.getByRole("button", { name: /delete selected \(2\)/i }));

    await waitFor(() => expect(deleteCalls).toHaveLength(2));
  });

  it("calls the delete-job endpoint when a failed upload is dismissed", async () => {
    let deleteJobCalled = false;
    stubAuthAndEmptyDocuments((url, init) => {
      if (url.includes("/ingestion/jobs/job-x") && init?.method === "DELETE") {
        deleteJobCalled = true;
        return new Response(null, { status: 204 });
      }
      if (url.includes("/ingestion/jobs/job-x")) {
        return new Response(JSON.stringify({ status: "failed", error: "boom" }), { status: 200 });
      }
      return null;
    });
    vi.spyOn(apiClient, "uploadWithProgress").mockResolvedValue({
      ok: true,
      status: 202,
      body: { job_id: "job-x" },
    });

    render(
      <AuthProvider>
        <DocumentsPage />
      </AuthProvider>
    );
    await waitFor(() => screen.getByText(/no documents uploaded yet/i));

    const file = new File(["%PDF-1.4"], "broken.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText(/choose pdf files/i) as HTMLInputElement;
    await userEvent.upload(input, file);
    await userEvent.click(screen.getByRole("button", { name: /upload 1 file/i }));

    await waitFor(() => expect(screen.getByText(/failed/i)).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    await waitFor(() => expect(deleteJobCalled).toBe(true));
  });
```

- [ ] **Step 2: Run tests to verify they pass**

Run (from `frontend/`): `npm test -- DocumentsPage`
Expected: 7 passed (3 from Task 6 + 4 new). Since Task 6 already implemented the behavior these
tests verify, no implementation step is needed here — if any test fails, it indicates a gap in
Task 6's implementation to fix in this task, not new functionality to add elsewhere.

- [ ] **Step 3: Type-check, lint, and run the full frontend suite**

Run: `npm run build && npm run lint && npm test`
Expected: all clean, all tests passing

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/DocumentsPage.test.tsx
git commit -m "test(frontend): cover bulk delete, delete confirmation, and dismiss cleanup (ERP-071, ERP-072)"
```

---

## After Implementation

Once all 7 tasks are committed:

1. Push the branch, open a PR to `develop`, wait for the CI `test` check, merge.
2. Deploy: backend via `gcloud compute ssh` → `git pull origin develop` + `systemctl restart
   rag-platform.service` (no migration needed — no schema change in this plan); frontend via
   `npx vercel --prod --scope bankar-ai` from `frontend/`.
3. Live-verify against the production deployment:
   - Upload a document via drag-drop, confirm it stages (doesn't upload) until "Upload" is
     clicked.
   - Click a citation, confirm the source panel renders real formatting (not literal `**`/`#`).
   - Copy a message, a conversation, and a source panel's text; confirm each lands on the
     clipboard.
   - Re-run the exact hallucination reproduction from Task 4's Step 5.
   - Select multiple documents and bulk-delete them, confirming the `window.confirm` dialog
     appears and names the files.
   - Confirm Delete/Retry/Dismiss button text is visible (ERP-074 fix).
   - Force a failed upload dismissal and confirm (via SSH/logs or a follow-up admin check) the
     job's temp file is actually gone, not just hidden client-side.
4. Clean up any test data created via the admin API.
5. Update `.ai/tickets/ERP-067.md` through `ERP-074.md` to `Status: Done` with Resolution
   sections, update `.ai/memory/current-state.md`'s "Next Planned Work", and write a new
   `.ai/sessions/2026-09-19-*.md` session log.
