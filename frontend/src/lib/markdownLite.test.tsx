import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
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
});
