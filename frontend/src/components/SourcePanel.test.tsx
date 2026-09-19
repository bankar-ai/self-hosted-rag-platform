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
