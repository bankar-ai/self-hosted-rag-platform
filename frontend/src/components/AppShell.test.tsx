import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../lib/AuthContext";
import AppShell from "./AppShell";

describe("AppShell", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("allows the header to wrap instead of forcing horizontal overflow", () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <AppShell>
            <div>content</div>
          </AppShell>
        </AuthProvider>
      </MemoryRouter>
    );
    const header = screen.getByText("Self-Hosted RAG Platform").closest("header");
    expect(header).not.toBeNull();
    expect(header?.className).toContain("flex-wrap");
  });
});
