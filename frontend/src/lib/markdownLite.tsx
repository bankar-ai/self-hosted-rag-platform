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
