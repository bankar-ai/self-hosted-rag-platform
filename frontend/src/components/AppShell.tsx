import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { apiFetch } from "../lib/apiClient";
import { useAuth } from "../lib/AuthContext";

function navLinkClass({ isActive }: { isActive: boolean }): string {
  return `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
    isActive ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"
  }`;
}

export default function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();

  async function handleDeleteAccount(): Promise<void> {
    const confirmed = window.confirm(
      "Deleting your account permanently removes your account and all your data " +
        "(documents, conversations, everything) with no way to undo it. Continue?"
    );
    if (!confirmed) return;
    await apiFetch("/auth/me", { method: "DELETE" });
    logout();
  }

  return (
    <div className="flex h-screen flex-col">
      <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-slate-200 bg-white px-6 py-3">
        <span className="text-lg font-semibold tracking-tight text-slate-900">
          Self-Hosted RAG Platform
        </span>
        <nav className="flex flex-wrap items-center gap-3">
          <NavLink to="/chat" className={navLinkClass}>
            Chat
          </NavLink>
          <NavLink to="/documents" className={navLinkClass}>
            Documents
          </NavLink>
          {user && (
            <span className="ml-2 truncate text-sm text-slate-500" title={user.email}>
              {user.email}
            </span>
          )}
          <button
            type="button"
            onClick={logout}
            className="rounded-md px-3 py-1.5 text-sm font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-900"
          >
            Log out
          </button>
          <button
            type="button"
            onClick={() => void handleDeleteAccount()}
            className="rounded-md px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50"
          >
            Delete account
          </button>
        </nav>
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}
