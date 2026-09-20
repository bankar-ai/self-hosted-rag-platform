import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Citation } from "./types";
import { renderMarkdownLite } from "./markdownLite";

describe("renderMarkdownLite", () => {
  it("renders **bold** text as a real <strong> element, not literal asterisks", () => {
    render(<div>{renderMarkdownLite("The code is **maple21**.")}</div>);

    expect(screen.getByText("maple21")).toBeInstanceOf(HTMLElement);
    expect(screen.getByText("maple21").tagName).toBe("STRONG");
    expect(screen.queryByText(/\*\*/)).toBeNull();
  });

  it("renders bullet lines as a real unordered list", () => {
    const { container } = render(
      <div>{renderMarkdownLite("Items:\n- First item\n- Second item")}</div>
    );

    const list = container.querySelector("ul");
    expect(list).not.toBeNull();
    const items = container.querySelectorAll("ul li");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toBe("First item");
    expect(items[1].textContent).toBe("Second item");
  });

  it("renders numbered lines as a real ordered list", () => {
    const { container } = render(
      <div>{renderMarkdownLite("Steps:\n1. Do this\n2. Then this")}</div>
    );

    const list = container.querySelector("ol");
    expect(list).not.toBeNull();
    const items = container.querySelectorAll("ol li");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toBe("Do this");
    expect(items[1].textContent).toBe("Then this");
  });

  it("renders plain paragraphs with no list markers as normal text", () => {
    render(<div>{renderMarkdownLite("Just a plain sentence.")}</div>);

    expect(screen.getByText("Just a plain sentence.")).toBeInstanceOf(HTMLElement);
  });

  it("handles bold text inside a bullet item", () => {
    const { container } = render(<div>{renderMarkdownLite("- The code is **maple21**")}</div>);

    const item = container.querySelector("li");
    expect(item?.querySelector("strong")?.textContent).toBe("maple21");
  });

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

  it("renders a [n] marker inside bold text as clickable too (ERP-077)", async () => {
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
    render(
      <div>{renderMarkdownLite("The answer is **yes [1]**.", [citation], onCitationClick)}</div>
    );

    const marker = screen.getByText("[1]");
    expect(marker.tagName).toBe("BUTTON");
    expect(marker.closest("strong")).not.toBeNull();
    await userEvent.click(marker);
    expect(onCitationClick).toHaveBeenCalledWith(citation);
  });
});
