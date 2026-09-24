import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it("deletes the account and logs out when confirmed", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <AuthProvider>
          <AppShell>
            <div>content</div>
          </AppShell>
        </AuthProvider>
      </MemoryRouter>
    );

    await userEvent.click(screen.getByRole("button", { name: /delete account/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/auth/me"),
        expect.objectContaining({ method: "DELETE" })
      );
    });
  });

  it("does not delete the account when the confirmation is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <AuthProvider>
          <AppShell>
            <div>content</div>
          </AppShell>
        </AuthProvider>
      </MemoryRouter>
    );

    await userEvent.click(screen.getByRole("button", { name: /delete account/i }));

    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/auth/me"),
      expect.anything()
    );
  });
});
