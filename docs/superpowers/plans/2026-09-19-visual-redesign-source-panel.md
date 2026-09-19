# Visual Redesign — Source Panel + Three-Pane Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user click any citation (inline `[n]` marker or the citation list below an answer) to open a right-hand panel showing that citation's exact source text, and restructure the Chat page into a NotebookLM-style three-pane layout (sources+history / chat / source detail), plus a small deliberate color/spacing pass replacing the current zero-customization Tailwind defaults.

**Architecture:** One new owner-scoped backend endpoint exposes a chunk's full text (already durably stored in Postgres, not previously exposed to the frontend). The frontend fetches it on demand only when a citation is clicked, renders it in a new `SourcePanel` component that becomes the third column of the layout, and a small regex pass in the existing hand-rolled markdown renderer turns `[n]` markers into buttons wired to the same panel. `ChatPage.tsx`'s sidebar JSX is extracted into its own `Sidebar` component as part of this work, since the file is already large and about to grow further. A final task adds a handful of custom Tailwind v4 `@theme` color tokens and applies them to existing elements — no layout change, no new dependency.

**Tech Stack:** FastAPI + SQLAlchemy (backend, unchanged stack), React + TypeScript + Tailwind v4 (frontend, unchanged stack). No new dependency anywhere in this plan.

**Spec:** `docs/superpowers/specs/2026-09-19-visual-redesign-source-panel-design.md`

## Global Constraints

- No new dependency (backend or frontend) — every requirement in this plan is met with the existing stack.
- No database migration — the new endpoint reads existing `chunks`/`documents` tables as-is.
- Every new/changed backend endpoint is owner-scoped via `get_current_user`, returning an undifferentiated `404` for "doesn't exist" and "exists but isn't yours" — matching this codebase's existing convention throughout `app/ingestion/router.py`.
- `Citation` (`frontend/src/lib/types.ts`) stays metadata-only — chunk `text` is fetched on demand via the new endpoint, never embedded in `Citation` or in any generation/retrieval response.
- No PDF storage, no PDF viewer, no sibling/section-expansion chunks in the panel, no "Studio"-style generated-artifacts pane — all explicitly out of scope per the spec.
- Frontend: `@` resolves to `frontend/src` (see `vite.config.ts`); existing convention in this codebase is `@/components/ui/...` via alias but `../lib/...` via relative path from `pages/`/`components/` — follow this exactly in new files.

---

### Task 1: Backend — `get_chunk_by_document_and_owner` repository function

**Files:**
- Modify: `app/ingestion/repository.py` (append after `get_sibling_chunks`, currently ending at line 175)
- Test: `tests/ingestion/test_repository.py`

**Interfaces:**
- Produces: `get_chunk_by_document_and_owner(session: Session, document_id: str, chunk_id: str, owner_id: uuid.UUID) -> ChunkRecord | None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/ingestion/test_repository.py`. First, update the existing import block (currently lines 5-13) to add the new function:

```python
from app.ingestion.repository import (
    delete_document,
    get_chunk_by_document_and_owner,
    get_chunks_by_vector_ids,
    get_sibling_chunks,
    get_vector_ids_for_documents,
    list_documents_for_owner,
    save_document_and_chunks,
    search_chunks_by_text,
)
```

Then append at the end of the file:

```python
def test_get_chunk_by_document_and_owner_returns_the_chunk():
    document_id = "doc-chunk-detail-test"
    chunks = [_chunk(document_id, 0, text="Alpha lives here.")]

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        chunk = get_chunk_by_document_and_owner(
            session, document_id, f"{document_id}-0", _TEST_OWNER_ID
        )

        assert chunk is not None
        assert chunk.text == "Alpha lives here."
        assert chunk.document_id == document_id


def test_get_chunk_by_document_and_owner_returns_none_for_unknown_chunk():
    session_factory = get_session_factory()
    with session_factory() as session:
        chunk = get_chunk_by_document_and_owner(
            session, "no-such-doc", "no-such-doc-0", _TEST_OWNER_ID
        )
        assert chunk is None


def test_get_chunk_by_document_and_owner_returns_none_for_another_owners_document():
    document_id = "doc-chunk-detail-cross-owner-test"
    chunks = [_chunk(document_id, 0)]
    other_owner_id = uuid.uuid4()

    session_factory = get_session_factory()
    with session_factory() as session:
        _ensure_test_owner(session)
        save_document_and_chunks(session, document_id, "doc.pdf", chunks, _TEST_OWNER_ID)
        session.commit()

    with session_factory() as session:
        chunk = get_chunk_by_document_and_owner(
            session, document_id, f"{document_id}-0", other_owner_id
        )
        assert chunk is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingestion/test_repository.py -k get_chunk_by_document_and_owner -v`
Expected: FAIL with `ImportError: cannot import name 'get_chunk_by_document_and_owner'`

- [ ] **Step 3: Implement the function**

Append to `app/ingestion/repository.py` (after `get_sibling_chunks`'s closing `]` at line 175):

```python


def get_chunk_by_document_and_owner(
    session: Session, document_id: str, chunk_id: str, owner_id: uuid.UUID
) -> ChunkRecord | None:
    """Fetch one chunk by its document and chunk ID, restricted to `owner_id`'s documents.

    Backs the frontend's source panel (ERP-050) -- returns `None` if the chunk doesn't exist,
    doesn't belong to `document_id`, or the document isn't owned by `owner_id`, one
    undifferentiated "not found" for all three cases, matching this module's existing
    cross-owner-access convention.
    """
    return session.scalar(
        select(ChunkRecord)
        .join(DocumentRecord, ChunkRecord.document_id == DocumentRecord.document_id)
        .where(
            ChunkRecord.chunk_id == chunk_id,
            ChunkRecord.document_id == document_id,
            DocumentRecord.owner_id == owner_id,
        )
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingestion/test_repository.py -k get_chunk_by_document_and_owner -v`
Expected: 3 passed

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check app/ingestion/repository.py tests/ingestion/test_repository.py && uv run mypy app/ingestion/repository.py`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/repository.py tests/ingestion/test_repository.py
git commit -m "feat(ingestion): add get_chunk_by_document_and_owner repository function (ERP-050)"
```

---

### Task 2: Backend — `ChunkDetailResponse` schema, service function, and endpoint

**Files:**
- Modify: `app/ingestion/schemas.py`
- Modify: `app/ingestion/service.py`
- Modify: `app/ingestion/router.py`
- Test: `tests/ingestion/test_router.py`

**Interfaces:**
- Consumes: `get_chunk_by_document_and_owner` from Task 1 (`app/ingestion/repository.py`)
- Produces: `ChunkDetailResponse` (Pydantic schema), `get_chunk_detail(document_id: str, chunk_id: str, owner_id: uuid.UUID) -> ChunkDetailResponse | None` (`app/ingestion/service.py`), `GET /documents/{document_id}/chunks/{chunk_id}` endpoint

- [ ] **Step 1: Write the failing tests**

Append to `tests/ingestion/test_router.py`:

```python
def test_get_chunk_returns_text_and_metadata(simple_text_pdf, auth_headers):
    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    upload = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    final = _poll_until_done(upload.json()["job_id"], auth_headers)
    document_id = final["result"]["document_id"]
    chunk_id = final["result"]["chunks"][0]["chunk_id"]

    response = client.get(f"/documents/{document_id}/chunks/{chunk_id}", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["chunk_id"] == chunk_id
    assert body["document_id"] == document_id
    assert body["text"] == final["result"]["chunks"][0]["text"]
    assert body["source_filename"] == "simple.pdf"


def test_get_chunk_404_for_unknown_chunk(simple_text_pdf, auth_headers):
    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    upload = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    final = _poll_until_done(upload.json()["job_id"], auth_headers)
    document_id = final["result"]["document_id"]

    response = client.get(f"/documents/{document_id}/chunks/does-not-exist", headers=auth_headers)
    assert response.status_code == 404


def test_get_chunk_404_for_chunk_belonging_to_another_user(simple_text_pdf, auth_headers):
    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    upload = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    final = _poll_until_done(upload.json()["job_id"], auth_headers)
    document_id = final["result"]["document_id"]
    chunk_id = final["result"]["chunks"][0]["chunk_id"]

    other_user_headers = register_and_login(client, "ingestion-chunk-detail-other-owner")
    response = client.get(
        f"/documents/{document_id}/chunks/{chunk_id}", headers=other_user_headers
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingestion/test_router.py -k test_get_chunk -v`
Expected: FAIL with 404 (route doesn't exist yet) on all three

- [ ] **Step 3: Add the schema**

Add to `app/ingestion/schemas.py`, after `DocumentListResponse`:

```python


class ChunkDetailResponse(BaseModel):
    """One chunk's full text and provenance, for the frontend's source panel (ERP-050)."""

    chunk_id: str
    document_id: str
    text: str
    section_path: list[str]
    page_start: int
    page_end: int
    source_filename: str
```

- [ ] **Step 4: Add the service function**

Modify `app/ingestion/service.py`'s imports (top of file):

```python
import uuid

from app.core.db import get_session_factory
from app.ingestion.chunker import chunk_markdown
from app.ingestion.config import IngestionSettings
from app.ingestion.parsers import parse_pdf
from app.ingestion.repository import get_chunk_by_document_and_owner, list_documents_for_owner
from app.ingestion.schemas import (
    Chunk,
    ChunkDetailResponse,
    DocumentListResponse,
    DocumentSummary,
    IngestResponse,
)
```

Append to `app/ingestion/service.py`:

```python


def get_chunk_detail(document_id: str, chunk_id: str, owner_id: uuid.UUID) -> ChunkDetailResponse | None:
    """Return one chunk's full text for the source panel (ERP-050).

    `None` if the chunk is unknown, belongs to a different document, or the document isn't
    owned by `owner_id` -- the router maps this to a `404`.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        record = get_chunk_by_document_and_owner(session, document_id, chunk_id, owner_id)
        if record is None:
            return None
        return ChunkDetailResponse(
            chunk_id=record.chunk_id,
            document_id=record.document_id,
            text=record.text,
            section_path=record.section_path,
            page_start=record.page_start,
            page_end=record.page_end,
            source_filename=record.source_filename,
        )
```

- [ ] **Step 5: Add the endpoint**

Modify `app/ingestion/router.py`'s imports:

```python
from app.ingestion.schemas import ChunkDetailResponse, DocumentListResponse, JobStatusResponse
from app.ingestion.service import get_chunk_detail, list_documents
```

Append to `app/ingestion/router.py`, after `delete_document_endpoint`:

```python


@documents_router.get("/{document_id}/chunks/{chunk_id}")
def get_chunk_endpoint(
    document_id: str, chunk_id: str, current_user: CurrentUser = Depends(get_current_user)
) -> ChunkDetailResponse:
    """Return one chunk's full text for the source panel. 404 if unknown or not owned by the caller."""
    chunk = get_chunk_detail(document_id, chunk_id, current_user.id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return chunk
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/ingestion/test_router.py -k test_get_chunk -v`
Expected: 3 passed

- [ ] **Step 7: Run the full backend suite, lint, and type-check**

Run: `uv run ruff check . && uv run mypy app/ && uv run pytest -q`
Expected: all clean, all tests passing (was 502 before this plan)

- [ ] **Step 8: Commit**

```bash
git add app/ingestion/schemas.py app/ingestion/service.py app/ingestion/router.py tests/ingestion/test_router.py
git commit -m "feat(ingestion): add GET /documents/{id}/chunks/{id} endpoint (ERP-050)"
```

---

### Task 3: Frontend — `ChunkDetail` type

**Files:**
- Modify: `frontend/src/lib/types.ts`

**Interfaces:**
- Produces: `ChunkDetail` interface, matching `ChunkDetailResponse` from Task 2

- [ ] **Step 1: Add the type**

Add to `frontend/src/lib/types.ts`, after `DocumentListResponse`:

```typescript
export interface ChunkDetail {
  chunk_id: string;
  document_id: string;
  text: string;
  section_path: string[];
  page_start: number;
  page_end: number;
  source_filename: string;
}
```

- [ ] **Step 2: Type-check**

Run: `npm run build` (from `frontend/`)
Expected: clean (this is an additive type, nothing consumes it yet)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/types.ts
git commit -m "feat(frontend): add ChunkDetail type (ERP-050)"
```

---

### Task 4: Frontend — `SourcePanel` component

**Files:**
- Create: `frontend/src/components/SourcePanel.tsx`
- Test: `frontend/src/components/SourcePanel.test.tsx`

**Interfaces:**
- Consumes: `Citation`, `ChunkDetail` (from `frontend/src/lib/types.ts`), `apiFetch` (from `frontend/src/lib/apiClient.ts`)
- Produces: `export default function SourcePanel({ citation, onClose }: { citation: Citation | null; onClose: () => void })`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/SourcePanel.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Citation } from "../lib/types";
import SourcePanel from "./SourcePanel";

const citation: Citation = {
  chunk_id: "doc1-0",
  document_id: "doc1",
  section_path: ["Introduction"],
  page_start: 1,
  page_end: 1,
  source_filename: "simple.pdf",
  score: 0.87,
  reranked: false,
};

describe("SourcePanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders nothing when no citation is selected", () => {
    const { container } = render(<SourcePanel citation={null} onClose={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("fetches and shows the chunk's text and metadata when a citation is selected", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            chunk_id: "doc1-0",
            document_id: "doc1",
            text: "The zorptastic quokkabird lives here.",
            section_path: ["Introduction"],
            page_start: 1,
            page_end: 1,
            source_filename: "simple.pdf",
          }),
          { status: 200 }
        )
      )
    );

    render(<SourcePanel citation={citation} onClose={() => {}} />);

    await waitFor(() =>
      expect(screen.getByText("The zorptastic quokkabird lives here.")).toBeInTheDocument()
    );
    expect(screen.getByText("simple.pdf")).toBeInTheDocument();
  });

  it("shows a 'no longer available' message on a 404", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));

    render(<SourcePanel citation={citation} onClose={() => {}} />);

    await waitFor(() =>
      expect(screen.getByText(/no longer available/i)).toBeInTheDocument()
    );
  });

  it("shows a retryable error on a network failure, and retries on click", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            chunk_id: "doc1-0",
            document_id: "doc1",
            text: "Recovered text.",
            section_path: [],
            page_start: 1,
            page_end: 1,
            source_filename: "simple.pdf",
          }),
          { status: 200 }
        )
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<SourcePanel citation={citation} onClose={() => {}} />);

    await waitFor(() => expect(screen.getByText(/couldn't load/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() => expect(screen.getByText("Recovered text.")).toBeInTheDocument());
  });

  it("calls onClose when the close button is clicked", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            chunk_id: "doc1-0",
            document_id: "doc1",
            text: "Some text.",
            section_path: [],
            page_start: 1,
            page_end: 1,
            source_filename: "simple.pdf",
          }),
          { status: 200 }
        )
      )
    );
    const onClose = vi.fn();

    render(<SourcePanel citation={citation} onClose={onClose} />);
    await waitFor(() => expect(screen.getByText("Some text.")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /close source panel/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- SourcePanel` (from `frontend/`)
Expected: FAIL — `SourcePanel` module doesn't exist yet

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/SourcePanel.tsx`:

```tsx
import { useEffect, useState } from "react";
import { apiFetch } from "../lib/apiClient";
import type { ChunkDetail, Citation } from "../lib/types";

interface SourcePanelProps {
  citation: Citation | null;
  onClose: () => void;
}

type PanelState =
  | { status: "loading" }
  | { status: "loaded"; text: string }
  | { status: "not-found" }
  | { status: "error" };

/**
 * Right-hand third pane (ERP-050): shows the exact source text a citation refers to. The
 * chunk's `text` is fetched on demand (not embedded in `Citation`) so every streamed answer
 * stays lean regardless of how many citations it carries. Page/section/score metadata comes
 * straight from `citation` -- only the text itself needs a network round trip.
 */
export default function SourcePanel({ citation, onClose }: SourcePanelProps) {
  const [state, setState] = useState<PanelState>({ status: "loading" });
  const [retryToken, setRetryToken] = useState(0);

  useEffect(() => {
    if (!citation) return;
    setState({ status: "loading" });
    void (async () => {
      try {
        const response = await apiFetch(
          `/documents/${citation.document_id}/chunks/${citation.chunk_id}`
        );
        if (response.status === 404) {
          setState({ status: "not-found" });
          return;
        }
        if (!response.ok) {
          setState({ status: "error" });
          return;
        }
        const chunk = (await response.json()) as ChunkDetail;
        setState({ status: "loaded", text: chunk.text });
      } catch {
        setState({ status: "error" });
      }
    })();
  }, [citation, retryToken]);

  if (!citation) return null;

  const pages =
    citation.page_start === citation.page_end
      ? `p. ${citation.page_start}`
      : `p. ${citation.page_start}-${citation.page_end}`;

  return (
    <aside className="flex w-80 shrink-0 flex-col overflow-y-auto border-l border-slate-200 bg-slate-50 p-4">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Source</p>
        <button
          className="rounded-md px-1.5 py-0.5 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-700"
          onClick={onClose}
          aria-label="Close source panel"
        >
          ✕
        </button>
      </div>

      {state.status === "loading" && (
        <div className="animate-pulse space-y-2">
          <div className="h-3 w-3/4 rounded bg-slate-200" />
          <div className="h-3 w-full rounded bg-slate-200" />
          <div className="h-3 w-5/6 rounded bg-slate-200" />
        </div>
      )}

      {state.status === "not-found" && (
        <p className="text-sm text-slate-500">This source is no longer available.</p>
      )}

      {state.status === "error" && (
        <div className="text-sm text-red-600">
          <p>Couldn&apos;t load this source.</p>
          <button className="mt-1 underline" onClick={() => setRetryToken((n) => n + 1)}>
            Retry
          </button>
        </div>
      )}

      {state.status === "loaded" && (
        <div>
          <p className="text-sm font-medium text-slate-900">{citation.source_filename}</p>
          <p className="mb-3 text-xs text-slate-400">
            {pages}
            {citation.section_path.length > 0 ? ` — ${citation.section_path.join(" / ")}` : ""}
          </p>
          <p className="whitespace-pre-wrap text-sm text-slate-700">{state.text}</p>
          <p className="mt-3 border-t border-slate-200 pt-2 text-xs text-slate-400">
            Relevance score: {citation.score.toFixed(3)}
            {citation.reranked ? " (reranked)" : " (retrieval fusion score)"}
          </p>
        </div>
      )}
    </aside>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test -- SourcePanel` (from `frontend/`)
Expected: 5 passed

- [ ] **Step 5: Lint and type-check**

Run: `npm run build && npm run lint` (from `frontend/`)
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SourcePanel.tsx frontend/src/components/SourcePanel.test.tsx
git commit -m "feat(frontend): add SourcePanel component (ERP-050)"
```

---

### Task 5: Frontend — clickable citation markers in `markdownLite`

**Files:**
- Modify: `frontend/src/lib/markdownLite.tsx`
- Modify: `frontend/src/lib/markdownLite.test.tsx`

**Interfaces:**
- Consumes: `Citation` (from `frontend/src/lib/types.ts`)
- Produces: `renderMarkdownLite(content: string, citations?: Citation[], onCitationClick?: (citation: Citation) => void): ReactNode` — the two new parameters are optional/defaulted, so every existing call site (`renderMarkdownLite(content)`) keeps working unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/lib/markdownLite.test.tsx` (add `vi` to the existing `vitest` import and `userEvent` as a new import):

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Citation } from "./types";
import { renderMarkdownLite } from "./markdownLite";
```

```tsx
  it("renders a [n] marker as a clickable element when it matches a citation", async () => {
    const citation: Citation = {
      chunk_id: "c1",
      document_id: "d1",
      section_path: ["Intro"],
      page_start: 1,
      page_end: 1,
      source_filename: "doc.pdf",
      score: 1,
      reranked: false,
    };
    const onCitationClick = vi.fn();
    render(<div>{renderMarkdownLite("See the answer [1].", [citation], onCitationClick)}</div>);

    const marker = screen.getByText("[1]");
    expect(marker.tagName).toBe("BUTTON");
    await userEvent.click(marker);
    expect(onCitationClick).toHaveBeenCalledWith(citation);
  });

  it("renders a [n] marker with no matching citation as plain text", () => {
    render(<div>{renderMarkdownLite("See [9] for details.", [])}</div>);

    const marker = screen.getByText("[9]");
    expect(marker.tagName).not.toBe("BUTTON");
  });
```

(Add these two `it` blocks inside the existing `describe("renderMarkdownLite", ...)` block.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- markdownLite` (from `frontend/`)
Expected: FAIL — `[1]`/`[9]` currently render as plain text nodes, not elements with a distinguishable tag, and `renderMarkdownLite` doesn't accept the new parameters yet (TypeScript error)

- [ ] **Step 3: Implement citation-marker parsing**

Replace the full contents of `frontend/src/lib/markdownLite.tsx`:

```tsx
import type { ReactNode } from "react";
import type { Citation } from "./types";

/**
 * Renders a small, known markdown subset (ERP-063): **bold**, "- "/"* " bullet lines, "1. "
 * numbered lines, paragraph breaks, and clickable [n] citation markers (ERP-050). Not a
 * general-purpose markdown renderer -- the model is only ever instructed to use this subset
 * (see `app/generation/prompt.py`'s SYSTEM_PROMPT), so a small local implementation covers it
 * exactly without adding a markdown/remark dependency.
 */

const BOLD_RE = /(\*\*[^*]+\*\*)/g;
const CITATION_MARKER_RE = /(\[\d+\])/g;
const CITATION_MARKER_EXACT_RE = /^\[(\d+)\]$/;
const BULLET_RE = /^\s*[-*]\s+(.*)$/;
const NUMBERED_RE = /^\s*\d+\.\s+(.*)$/;

function renderTextWithCitations(
  text: string,
  keyPrefix: string,
  citations: Citation[],
  onCitationClick: (citation: Citation) => void
): ReactNode[] {
  const parts = text.split(CITATION_MARKER_RE).filter((part) => part.length > 0);
  return parts.map((part, index) => {
    const match = CITATION_MARKER_EXACT_RE.exec(part);
    const citation = match ? citations[Number(match[1]) - 1] : undefined;
    if (citation) {
      return (
        <button
          key={`${keyPrefix}-${index}`}
          type="button"
          className="mx-0.5 rounded bg-slate-200 px-1 text-xs font-medium text-slate-700 hover:bg-slate-300"
          onClick={() => onCitationClick(citation)}
        >
          {part}
        </button>
      );
    }
    return <span key={`${keyPrefix}-${index}`}>{part}</span>;
  });
}

function renderInline(
  text: string,
  keyPrefix: string,
  citations: Citation[],
  onCitationClick: (citation: Citation) => void
): ReactNode[] {
  const parts = text.split(BOLD_RE).filter((part) => part.length > 0);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={`${keyPrefix}-${index}`}>{part.slice(2, -2)}</strong>;
    }
    return (
      <span key={`${keyPrefix}-${index}`}>
        {renderTextWithCitations(part, `${keyPrefix}-${index}`, citations, onCitationClick)}
      </span>
    );
  });
}

const NOOP_CITATION_CLICK = () => {};

export function renderMarkdownLite(
  content: string,
  citations: Citation[] = [],
  onCitationClick: (citation: Citation) => void = NOOP_CITATION_CLICK
): ReactNode {
  const lines = content.split("\n");
  const blocks: ReactNode[] = [];
  let currentList: { type: "ul" | "ol"; items: string[] } | null = null;
  let paragraphLines: string[] = [];

  function flushParagraph(key: string): void {
    if (paragraphLines.length === 0) return;
    blocks.push(
      <p key={key} className="whitespace-pre-wrap">
        {renderInline(paragraphLines.join("\n"), key, citations, onCitationClick)}
      </p>
    );
    paragraphLines = [];
  }

  function flushList(key: string): void {
    if (!currentList) return;
    const { type, items } = currentList;
    const className = type === "ul" ? "list-disc pl-5" : "list-decimal pl-5";
    const children = items.map((item, index) => (
      <li key={`${key}-${index}`}>
        {renderInline(item, `${key}-${index}`, citations, onCitationClick)}
      </li>
    ));
    blocks.push(
      type === "ul" ? (
        <ul key={key} className={className}>
          {children}
        </ul>
      ) : (
        <ol key={key} className={className}>
          {children}
        </ol>
      )
    );
    currentList = null;
  }

  lines.forEach((line, index) => {
    const bulletMatch = BULLET_RE.exec(line);
    const numberedMatch = NUMBERED_RE.exec(line);

    if (bulletMatch) {
      flushParagraph(`p-${index}`);
      if (currentList && currentList.type !== "ul") flushList(`l-${index}`);
      if (!currentList) currentList = { type: "ul", items: [] };
      currentList.items.push(bulletMatch[1]);
    } else if (numberedMatch) {
      flushParagraph(`p-${index}`);
      if (currentList && currentList.type !== "ol") flushList(`l-${index}`);
      if (!currentList) currentList = { type: "ol", items: [] };
      currentList.items.push(numberedMatch[1]);
    } else if (line.trim() === "") {
      flushList(`l-${index}`);
      flushParagraph(`p-${index}`);
    } else {
      flushList(`l-${index}`);
      paragraphLines.push(line);
    }
  });
  flushList("l-end");
  flushParagraph("p-end");

  return <>{blocks}</>;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test -- markdownLite` (from `frontend/`)
Expected: all passed (5 existing + 2 new)

- [ ] **Step 5: Lint and type-check**

Run: `npm run build && npm run lint` (from `frontend/`)
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/markdownLite.tsx frontend/src/lib/markdownLite.test.tsx
git commit -m "feat(frontend): make [n] citation markers clickable (ERP-050)"
```

---

### Task 6: Frontend — extract `Sidebar` component

Pure refactor — no behavior change. `ChatPage.tsx` is already ~470 lines; this pulls out the sidebar JSX (recent conversations + documents-with-checkboxes) it already renders today into its own file so the next task can add a third column without the file growing further.

**Files:**
- Create: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/pages/ChatPage.tsx`

**Interfaces:**
- Produces: `SidebarConversation`, `SidebarDocument` interfaces (moved from `ChatPage.tsx`), `export default function Sidebar(props: SidebarProps)`
- Consumes (by `ChatPage.tsx`): the above, imported from `../components/Sidebar`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/Sidebar.tsx`:

```tsx
import { Button } from "@/components/ui/button";

export interface SidebarConversation {
  id: string;
  title: string;
}

export interface SidebarDocument {
  id: string;
  title: string;
}

interface SidebarProps {
  recentConversations: SidebarConversation[];
  activeConversationId: string;
  onSelectConversation: (id: string) => void;
  onRenameConversation: (conv: SidebarConversation) => void;
  onNewConversation: () => void;
  documents: SidebarDocument[];
  deselectedDocumentIds: Set<string>;
  onToggleDocument: (id: string) => void;
}

/** Recent conversations + documents-with-checkboxes (ERP-044); the left pane of the chat's
 * three-pane layout (ERP-050). Extracted from `ChatPage.tsx` unchanged in behavior. */
export default function Sidebar({
  recentConversations,
  activeConversationId,
  onSelectConversation,
  onRenameConversation,
  onNewConversation,
  documents,
  deselectedDocumentIds,
  onToggleDocument,
}: SidebarProps) {
  return (
    <aside className="flex w-64 shrink-0 flex-col overflow-y-auto border-r border-slate-200 bg-slate-50 p-4">
      <Button className="mb-4 w-full" onClick={onNewConversation}>
        New chat
      </Button>
      <p className="mb-2 px-1 text-xs font-medium uppercase tracking-wide text-slate-400">
        Recent conversations
      </p>
      {recentConversations.length === 0 ? (
        <p className="px-1 text-sm text-slate-400">No conversations yet.</p>
      ) : (
        <ul className="mb-6 flex flex-col gap-1">
          {recentConversations.map((conv) => (
            <li key={conv.id} className="flex items-center gap-1">
              <button
                className={`min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-slate-200 ${
                  conv.id === activeConversationId ? "bg-slate-200 font-medium" : "text-slate-700"
                }`}
                onClick={() => onSelectConversation(conv.id)}
              >
                {conv.title}
              </button>
              <button
                className="shrink-0 rounded-md px-1.5 py-1 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-700"
                title="Rename conversation"
                onClick={() => onRenameConversation(conv)}
              >
                ✎
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="mb-2 px-1 text-xs font-medium uppercase tracking-wide text-slate-400">
        Your documents
      </p>
      {documents.length === 0 ? (
        <p className="px-1 text-sm text-slate-400">
          No documents uploaded yet — visit Documents to add one.
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {documents.map((doc) => {
            const isSelected = !deselectedDocumentIds.has(doc.id);
            return (
              <li key={doc.id}>
                <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-sm text-slate-600 hover:bg-slate-200">
                  <input
                    type="checkbox"
                    className="h-3.5 w-3.5 shrink-0 accent-emerald-600"
                    checked={isSelected}
                    onChange={() => onToggleDocument(doc.id)}
                  />
                  <span className="truncate" title={doc.title}>
                    {doc.title}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
      {documents.length > 0 && (
        <p className="mt-1 px-2 text-xs text-slate-400">
          Answers are grounded only in checked documents.
        </p>
      )}
      {documents.length > 0 && deselectedDocumentIds.size === documents.length && (
        <p className="mt-1 px-2 text-xs text-amber-600">
          No documents selected — questions won&apos;t find any answers.
        </p>
      )}
    </aside>
  );
}
```

- [ ] **Step 2: Update `ChatPage.tsx` to use it**

In `frontend/src/pages/ChatPage.tsx`:

1. Remove the local `interface SidebarConversation { ... }` and `interface SidebarDocument { ... }` blocks (currently lines 23-31).
2. Add an import: `import Sidebar, { type SidebarConversation, type SidebarDocument } from "../components/Sidebar";`
3. Replace the entire `<aside className="flex w-64 ...">...</aside>` block (currently lines 307-378) with:

```tsx
      <Sidebar
        recentConversations={recentConversations}
        activeConversationId={conversationId}
        onSelectConversation={(id) => void selectConversation(id)}
        onRenameConversation={handleRename}
        onNewConversation={startNewConversation}
        documents={documents}
        deselectedDocumentIds={deselectedDocumentIds}
        onToggleDocument={toggleDocumentSelected}
      />
```

- [ ] **Step 3: Type-check, lint, and run the frontend suite**

Run: `npm run build && npm run lint && npm test` (from `frontend/`)
Expected: all clean, all 29 existing tests still passing (no behavior change, `ChatPage.tsx` still has no dedicated test file)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/pages/ChatPage.tsx
git commit -m "refactor(frontend): extract Sidebar component from ChatPage (ERP-050)"
```

---

### Task 7: Frontend — wire `SourcePanel` into `ChatPage` (three-pane layout)

**Files:**
- Modify: `frontend/src/pages/ChatPage.tsx`

**Interfaces:**
- Consumes: `SourcePanel` (Task 4), `renderMarkdownLite`'s new signature (Task 5), `Sidebar` (Task 6)

- [ ] **Step 1: Add `SourcePanel` import and `selectedCitation` state**

In `frontend/src/pages/ChatPage.tsx`, add the import alongside the `Sidebar` import from Task 6:

```tsx
import SourcePanel from "../components/SourcePanel";
```

Add new state next to the existing `deselectedDocumentIds` state:

```tsx
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);
```

- [ ] **Step 2: Clear the selected citation when switching conversations**

In `startNewConversation`, add `setSelectedCitation(null);` after `setMessages([]);`.

In `selectConversation`, add `setSelectedCitation(null);` right after `setConversationId(id);` (before `await loadConversationHistory(id);`) — an open source panel referencing the previous conversation's citation shouldn't survive a conversation switch.

- [ ] **Step 3: Pass citations into `renderMarkdownLite` and wire click handlers**

Replace:

```tsx
                    <div className="text-sm">{renderMarkdownLite(message.content)}</div>
```

with:

```tsx
                    <div className="text-sm">
                      {renderMarkdownLite(message.content, message.citations ?? [], setSelectedCitation)}
                    </div>
```

Replace the citation list's `<li>` block:

```tsx
                        <li key={citation.chunk_id}>
                          <details>
                            <summary className="cursor-pointer">
                              [{citationIndex + 1}] {formatCitation(citation)}
                            </summary>
                            <p className="mt-0.5 pl-3 text-slate-400">
                              Relevance score: {citation.score.toFixed(3)}
                              {citation.reranked ? " (reranked)" : " (retrieval fusion score)"}
                            </p>
                          </details>
                        </li>
```

with:

```tsx
                        <li key={citation.chunk_id}>
                          <button
                            type="button"
                            className="text-left hover:text-slate-700 hover:underline"
                            onClick={() => setSelectedCitation(citation)}
                          >
                            [{citationIndex + 1}] {formatCitation(citation)}
                          </button>
                        </li>
```

(The relevance-score detail this `<details>` used to show on expand now lives in `SourcePanel` itself, which receives the full `Citation` — including `score`/`reranked` — so nothing is lost, just consolidated into the one place clicking now takes you.)

- [ ] **Step 4: Render `SourcePanel` as the third column**

At the end of the top-level `<div className="flex h-full">`, after the closing `</main>` tag, add:

```tsx
      <SourcePanel citation={selectedCitation} onClose={() => setSelectedCitation(null)} />
```

- [ ] **Step 5: Type-check and lint**

Run: `npm run build && npm run lint` (from `frontend/`)
Expected: both clean

- [ ] **Step 6: Manual live verification**

This is the one behavior change in the plan with no existing `ChatPage.tsx` test harness to extend (consistent with how ERP-044's `ChatPage.tsx` change was verified — see the spec's Testing section). Verify against the local dev stack:

1. Ensure the dev stack is running (see the `dev-stack` skill: `docker compose up -d` from the repo root) and start the backend (`uv run uvicorn app.main:app --reload`) and frontend (`npm run dev` from `frontend/`).
2. Log in, upload a PDF, send a query that gets a cited answer.
3. Confirm: the citation list below the answer is clickable and opens the right-hand panel with the correct text; an inline `[1]` marker in the answer text is also clickable and opens the same panel; the panel shows filename/page/relevance score; closing the panel (✕) removes the third column; switching conversations closes an open panel.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/ChatPage.tsx
git commit -m "feat(frontend): wire SourcePanel into ChatPage's three-pane layout (ERP-050)"
```

---

### Task 8: Cosmetic — `@theme` accent tokens

**Files:**
- Modify: `frontend/src/index.css`
- Modify: `frontend/src/components/ui/button.tsx`
- Modify: `frontend/src/pages/ChatPage.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

No new test — this is a pure styling pass (colors only, no layout/behavior change); verified via type-check/lint/existing test suite plus a manual visual check, consistent with how style-only changes are handled elsewhere in this repo.

- [ ] **Step 1: Add the theme tokens**

Replace the contents of `frontend/src/index.css`:

```css
@import "tailwindcss";

@theme {
  /* A single deliberate accent color (indigo), replacing the ad hoc slate/emerald choices
     picked file-by-file during the original ERP-043 build -- this is the concrete fix for
     "very basic looks" (ERP-050): one consistent color for primary actions and active state,
     not a new design system. */
  --color-brand: #4f46e5;
  --color-brand-dark: #4338ca;
}
```

- [ ] **Step 2: Apply to the shared `Button` component**

In `frontend/src/components/ui/button.tsx`, replace `bg-slate-900` with `bg-brand` and `hover:bg-slate-700` with `hover:bg-brand-dark`:

```tsx
import type { ButtonHTMLAttributes } from "react";

export function Button({ className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={`inline-flex items-center justify-center rounded-md bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-dark disabled:pointer-events-none disabled:opacity-50 ${className}`}
      {...props}
    />
  );
}
```

(This is the only edit `Button` needs — `LoginPage.tsx` and `DocumentsPage.tsx` both already use this shared component, so they pick up the new color automatically with no direct edit, per the spec's explicit choice not to do a dedicated redesign pass on those two pages.)

- [ ] **Step 3: Apply to the chat message bubble**

In `frontend/src/pages/ChatPage.tsx`, in the message bubble `className` ternary, replace `"ml-auto max-w-[80%] rounded-2xl rounded-br-sm bg-slate-900 px-4 py-2 text-white"` with `"ml-auto max-w-[80%] rounded-2xl rounded-br-sm bg-brand px-4 py-2 text-white"`.

- [ ] **Step 4: Apply to the sidebar's active state**

In `frontend/src/components/Sidebar.tsx`, replace:

```tsx
                className={`min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-slate-200 ${
                  conv.id === activeConversationId ? "bg-slate-200 font-medium" : "text-slate-700"
                }`}
```

with:

```tsx
                className={`min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-slate-200 ${
                  conv.id === activeConversationId
                    ? "bg-brand/10 font-medium text-brand-dark"
                    : "text-slate-700"
                }`}
```

Also replace the document checkbox's `accent-emerald-600` with `accent-brand`.

- [ ] **Step 5: Type-check, lint, and run the frontend suite**

Run: `npm run build && npm run lint && npm test` (from `frontend/`)
Expected: all clean, all existing tests still passing (this task changes only Tailwind class names, no logic)

- [ ] **Step 6: Manual visual check**

With the local dev server running (from Task 7's Step 6), confirm: the send button, "New chat" button, and user message bubbles show the new indigo accent; the active conversation in the sidebar has a subtle indigo-tinted background instead of plain gray; document checkboxes tint indigo when checked.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/index.css frontend/src/components/ui/button.tsx frontend/src/pages/ChatPage.tsx frontend/src/components/Sidebar.tsx
git commit -m "style(frontend): add a deliberate accent color via Tailwind @theme (ERP-050)"
```

---

## After Implementation

Once all 8 tasks are committed:

1. Push the branch, open a PR to `develop`, wait for the CI `test` check, merge.
2. Deploy: backend via `gcloud compute ssh` → `git pull origin develop` + `systemctl restart rag-platform.service` (no migration needed); frontend via `npx vercel --prod` from `frontend/`.
3. Live-verify against the production deployment: upload a real document, ask a question that gets cited, confirm both citation triggers open the panel with correct text, confirm a citation for a since-deleted document shows "no longer available", confirm the visual accent color is live. Clean up any test data created via the admin API.
4. Update `.ai/tickets/ERP-050.md` to `Status: Done` with a Resolution section, update `.ai/memory/current-state.md`'s "Next Planned Work", and write a new `.ai/sessions/2026-09-19-*.md` session log — same closeout pattern as every other ticket in this project.
