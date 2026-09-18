import type { ReactNode } from "react";

/**
 * Renders a small, known markdown subset (ERP-063): **bold**, "- "/"* " bullet lines, "1. "
 * numbered lines, and paragraph breaks. Not a general-purpose markdown renderer -- the model is
 * only ever instructed to use this subset (see `app/generation/prompt.py`'s SYSTEM_PROMPT), so a
 * small local implementation covers it exactly without adding a markdown/remark dependency.
 */

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter((part) => part.length > 0);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={`${keyPrefix}-${index}`}>{part.slice(2, -2)}</strong>;
    }
    return <span key={`${keyPrefix}-${index}`}>{part}</span>;
  });
}

const BULLET_RE = /^\s*[-*]\s+(.*)$/;
const NUMBERED_RE = /^\s*\d+\.\s+(.*)$/;

export function renderMarkdownLite(content: string): ReactNode {
  const lines = content.split("\n");
  const blocks: ReactNode[] = [];
  let currentList: { type: "ul" | "ol"; items: string[] } | null = null;
  let paragraphLines: string[] = [];

  function flushParagraph(key: string): void {
    if (paragraphLines.length === 0) return;
    blocks.push(
      <p key={key} className="whitespace-pre-wrap">
        {renderInline(paragraphLines.join("\n"), key)}
      </p>
    );
    paragraphLines = [];
  }

  function flushList(key: string): void {
    if (!currentList) return;
    const { type, items } = currentList;
    const className = type === "ul" ? "list-disc pl-5" : "list-decimal pl-5";
    const children = items.map((item, index) => (
      <li key={`${key}-${index}`}>{renderInline(item, `${key}-${index}`)}</li>
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
