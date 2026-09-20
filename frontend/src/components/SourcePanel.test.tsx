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
    expect(screen.getByText("simple.pdf")).toBeInTheDocument();
    expect(screen.getByText(/relevance score: 0\.870/i)).toBeInTheDocument();
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
    expect(screen.getByText("simple.pdf")).toBeInTheDocument();
    expect(screen.getByText(/relevance score: 0\.870/i)).toBeInTheDocument();
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

  it("has dialog role/aria-label and moves focus into itself on open (ERP-079)", async () => {
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

    render(<SourcePanel citation={citation} onClose={() => {}} />);

    const dialog = await screen.findByRole("dialog", { name: /simple\.pdf/i });
    await waitFor(() => expect(dialog).toHaveFocus());
  });

  it("calls onClose when Escape is pressed", async () => {
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
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog).toHaveFocus());

    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("strips markdown/HTML decoration from the section_path breadcrumb", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            chunk_id: "doc1-0",
            document_id: "doc1",
            text: "Some text.",
            section_path: ["**Prevent Falls**", "<mark>Ladder Falls</mark>"],
            page_start: 4,
            page_end: 5,
            source_filename: "simple.pdf",
          }),
          { status: 200 }
        )
      )
    );

    render(
      <SourcePanel
        citation={{ ...citation, section_path: ["**Prevent Falls**", "<mark>Ladder Falls</mark>"] }}
        onClose={() => {}}
      />
    );

    await waitFor(() => expect(screen.getByText("Some text.")).toBeInTheDocument());

    expect(screen.getByText(/Prevent Falls \/ Ladder Falls/)).toBeInTheDocument();
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
});
