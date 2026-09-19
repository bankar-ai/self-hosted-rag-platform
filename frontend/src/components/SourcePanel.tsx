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
