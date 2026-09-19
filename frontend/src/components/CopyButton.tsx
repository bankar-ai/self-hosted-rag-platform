import { useState } from "react";

interface CopyButtonProps {
  getText: () => string;
  label?: string;
  className?: string;
}

/** A small reusable copy-to-clipboard button (ERP-068), used for a single answer, a whole
 * conversation, and a source panel's chunk text -- `getText` is called at click time (not
 * render time) so the copied content always reflects the latest state. */
export default function CopyButton({ getText, label = "Copy", className = "" }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  async function handleClick(): Promise<void> {
    try {
      await navigator.clipboard.writeText(getText());
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // best-effort -- clipboard access can be denied by the browser; no user-facing error
    }
  }

  return (
    <button type="button" className={className} onClick={() => void handleClick()}>
      {copied ? "Copied!" : label}
    </button>
  );
}
