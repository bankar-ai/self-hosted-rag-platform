const CONFIDENCE_STYLES: Record<string, string> = {
  high: "bg-emerald-100 text-emerald-700",
  excellent: "bg-emerald-100 text-emerald-700",
  good: "bg-emerald-100 text-emerald-700",
  fair: "bg-amber-100 text-amber-700",
  poor: "bg-red-100 text-red-700",
};
const FALLBACK_STYLE = "bg-slate-100 text-slate-500";

/** A small parsing-confidence badge (ERP-076) -- a consolidated, document-level label (not
 * per-page detail) so a caller can tell at a glance whether a document parsed reliably.
 * "high" only ever comes from the fast path (no OCR fallback needed); the rest are docling's
 * own document-level `mean_grade` for a document that went through the OCR fallback. Any
 * other/unrecognized value (including docling's "unspecified") falls back to a neutral style. */
export default function ConfidenceBadge({ confidence }: { confidence: string }) {
  const style = CONFIDENCE_STYLES[confidence] ?? FALLBACK_STYLE;
  return (
    <span
      className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium capitalize ${style}`}
      title={`Parsing confidence: ${confidence}`}
    >
      {confidence}
    </span>
  );
}
