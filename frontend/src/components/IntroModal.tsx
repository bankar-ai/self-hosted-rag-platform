import { useState } from "react";
import { Link } from "react-router-dom";
import { CAN_DO, CANNOT_DO } from "../lib/introContent";

const STORAGE_KEY = "introSeen_v1";

export default function IntroModal() {
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === "true";
    } catch {
      return false;
    }
  });

  if (dismissed) return null;

  function handleDismiss(): void {
    try {
      localStorage.setItem(STORAGE_KEY, "true");
    } catch {
      // localStorage can throw in a private-window/blocked-storage context -- the modal still
      // dismisses for this render, it just won't be remembered next visit. Not load-bearing.
    }
    setDismissed(true);
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Welcome"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="max-h-[80vh] w-full max-w-md overflow-y-auto rounded-xl bg-white p-6">
        <h2 className="mb-1 text-lg font-semibold text-slate-900">Welcome</h2>
        <p className="mb-4 text-sm text-slate-500">A quick guide before you start.</p>

        <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-400">
          You can
        </h3>
        <ul className="mb-4 list-disc space-y-1 pl-5 text-sm text-slate-700">
          {CAN_DO.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>

        <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-400">
          Good to know
        </h3>
        <ul className="mb-6 list-disc space-y-1 pl-5 text-sm text-slate-700">
          {CANNOT_DO.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>

        <div className="flex items-center justify-between">
          <Link to="/about" onClick={handleDismiss} className="text-sm text-brand underline">
            Read the full guide
          </Link>
          <button
            type="button"
            onClick={handleDismiss}
            className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-white"
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
