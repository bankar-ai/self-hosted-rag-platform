import { useState } from "react";

interface CopyButtonProps {
  getText: () => string | Promise<string>;
  label?: string;
  className?: string;
  title?: string;
}

/** A small reusable copy-to-clipboard button (ERP-068), used for a single answer, a whole
 * conversation (including one fetched on demand from the sidebar, ERP-075), a Q&A turn, and a
 * source panel's chunk text -- `getText` is called at click time (not render time) so the
 * copied content always reflects the latest state. May return a `Promise<string>` when the
 * text has to be fetched first (e.g. a sidebar conversation that isn't the open one). */
export default function CopyButton({
  getText,
  label = "Copy",
  className = "",
  title,
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  async function handleClick(): Promise<void> {
    try {
      await navigator.clipboard.writeText(await getText());
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // best-effort -- clipboard access can be denied by the browser; no user-facing error
    }
  }

  return (
    <button type="button" className={className} title={title} onClick={() => void handleClick()}>
      {copied ? "Copied!" : label}
    </button>
  );
}
