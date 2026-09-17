import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../lib/AuthContext";

function navLinkClass({ isActive }: { isActive: boolean }): string {
  return `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
    isActive ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"
  }`;
}

export default function AppShell({ children }: { children: ReactNode }) {
  const { logout } = useAuth();

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
        <span className="text-lg font-semibold tracking-tight text-slate-900">
          Self-Hosted RAG Platform
        </span>
        <nav className="flex items-center gap-2">
          <NavLink to="/chat" className={navLinkClass}>
            Chat
          </NavLink>
          <NavLink to="/documents" className={navLinkClass}>
            Documents
          </NavLink>
          <button
            type="button"
            onClick={logout}
            className="ml-2 rounded-md px-3 py-1.5 text-sm font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-900"
          >
            Log out
          </button>
        </nav>
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}
