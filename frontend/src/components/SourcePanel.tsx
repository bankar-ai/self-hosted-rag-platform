import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import { apiFetch } from "../lib/apiClient";
import type { ChunkDetail, Citation } from "../lib/types";
import CopyButton from "./CopyButton";

interface SourcePanelProps {
  citation: Citation | null;
  onClose: () => void;
}

type PanelState =
  | { status: "loading" }
  | { status: "loaded"; text: string }
  | { status: "not-found" }
  | { status: "error" };

// PDF parsing sometimes emits <mark>/<u> for highlighted/underlined text -- rehype-sanitize's
// default schema doesn't allow either tag, so both would otherwise be stripped along with any
// genuinely unsafe markup. Extending the allow-list, not replacing it, keeps everything else
// (script tags, event handlers, etc.) sanitized away as normal.
const sanitizeSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "mark", "u"],
};

// A section heading extracted from a PDF sometimes carries the same markdown/HTML decoration
// (**bold**, <mark>...</mark>) the chunker's markdown export uses for detected headings --
// fine for the chunk body (rendered via ReactMarkdown below), but citation.section_path is
// shown as a plain-text breadcrumb, never markdown-rendered, so that decoration shows up as
// literal syntax instead of being stripped. Strips just enough to read cleanly as plain text.
function stripMarkdownDecoration(text: string): string {
  return text.replace(/\*\*/g, "").replace(/<\/?[a-zA-Z][^>]*>/g, "").trim();
}

/**
 * Right-hand third pane (ERP-050): shows the exact source text a citation refers to. The
 * chunk's `text` is fetched on demand (not embedded in `Citation`) so every streamed answer
 * stays lean regardless of how many citations it carries. Page/section/score metadata comes
 * straight from `citation` -- only the text itself needs a network round trip.
 */
export default function SourcePanel({ citation, onClose }: SourcePanelProps) {
  const [state, setState] = useState<PanelState>({ status: "loading" });
  const [retryToken, setRetryToken] = useState(0);
  const panelRef = useRef<HTMLElement>(null);

  // ERP-079: move focus into the panel as soon as it opens, so keyboard/screen-reader users
  // land on it instead of it silently appearing off to the side.
  useEffect(() => {
    if (citation) panelRef.current?.focus();
  }, [citation]);

  // ERP-079: Escape closes the panel; Tab/Shift+Tab are trapped among the panel's own
  // focusable elements so focus can't silently leave into the chat behind it.
  function handleKeyDown(event: KeyboardEvent<HTMLElement>): void {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab" || !panelRef.current) return;
    const focusable = panelRef.current.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  useEffect(() => {
    if (!citation) return;
    let cancelled = false;
    setState({ status: "loading" });
    void (async () => {
      try {
        const response = await apiFetch(
          `/documents/${citation.document_id}/chunks/${citation.chunk_id}`
        );
        if (cancelled) return;
        if (response.status === 404) {
          setState({ status: "not-found" });
          return;
        }
        if (!response.ok) {
          setState({ status: "error" });
          return;
        }
        const chunk = (await response.json()) as ChunkDetail;
        if (cancelled) return;
        setState({ status: "loaded", text: chunk.text });
      } catch {
        if (!cancelled) setState({ status: "error" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [citation, retryToken]);

  if (!citation) return null;

  const pages =
    citation.page_start === citation.page_end
      ? `p. ${citation.page_start}`
      : `p. ${citation.page_start}-${citation.page_end}`;

  return (
    <aside
      ref={panelRef}
      role="dialog"
      aria-modal="true"
      aria-label={`Source: ${citation.source_filename}`}
      tabIndex={-1}
      onKeyDown={handleKeyDown}
      className="fixed inset-0 z-40 flex w-full flex-col overflow-x-hidden overflow-y-auto border-l border-slate-200 bg-slate-50 p-4 focus:outline-none md:static md:inset-auto md:z-auto md:w-80 md:shrink-0"
    >
      <div className="sticky top-0 z-10 pb-3 flex items-center justify-between bg-slate-50">
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

      <p className="text-sm font-medium text-slate-900">{citation.source_filename}</p>
      <p className="mb-3 break-words text-xs text-slate-400">
        {pages}
        {citation.section_path.length > 0
          ? ` — ${citation.section_path.map(stripMarkdownDecoration).join(" / ")}`
          : ""}
      </p>

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
        <div className="break-words text-sm text-slate-700 [&_h1]:mb-1 [&_h1]:mt-3 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mb-1 [&_h2]:mt-3 [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:mb-1 [&_h3]:mt-2 [&_h3]:text-sm [&_h3]:font-semibold [&_h4]:mb-1 [&_h4]:mt-2 [&_h4]:text-sm [&_h4]:font-semibold [&_h5]:mb-1 [&_h5]:mt-2 [&_h5]:text-sm [&_h5]:font-semibold [&_p]:mb-2 [&_ul]:mb-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:mb-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_mark]:bg-yellow-200 [&_mark]:px-0.5 [&_u]:underline">
          <ReactMarkdown rehypePlugins={[rehypeRaw, [rehypeSanitize, sanitizeSchema]]}>
            {state.text}
          </ReactMarkdown>
        </div>
      )}

      <p className="mt-3 border-t border-slate-200 pt-2 text-xs text-slate-400">
        Relevance score: {citation.score.toFixed(3)}
        {citation.reranked ? " (reranked)" : " (retrieval fusion score)"}
      </p>
    </aside>
  );
}
