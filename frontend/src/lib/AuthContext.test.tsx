import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SESSION_EXPIRED_EVENT } from "./apiClient";
import { AuthProvider, useAuth } from "./AuthContext";
import { setStoredUserId } from "./tokenStorage";

function UserEmailProbe() {
  const { user } = useAuth();
  return <p>{user ? user.email : "no user yet"}</p>;
}

function AuthStatusProbe() {
  const { isAuthenticated } = useAuth();
  return <p>{isAuthenticated ? "authenticated" : "not authenticated"}</p>;
}

describe("AuthContext", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches and exposes the current user on mount when tokens already exist", async () => {
    setStoredUserId("u1");
    const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ id: "u1", email: "existing@example.com", role: "user", is_active: true }),
        { status: 200 }
      )
    );

    render(
      <AuthProvider>
        <UserEmailProbe />
      </AuthProvider>
    );

    expect(await screen.findByText("existing@example.com")).toBeInTheDocument();
  });

  it("does not fetch a user when no tokens are stored", () => {
    render(
      <AuthProvider>
        <UserEmailProbe />
      </AuthProvider>
    );

    expect(screen.getByText("no user yet")).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("logs out immediately when the session expires elsewhere (ERP-116)", async () => {
    setStoredUserId("u1");
    const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;
    // logout() itself fires a best-effort POST /auth/logout as a side effect -- default every
    // call to succeed so that fire-and-forget request doesn't hit an unmocked `undefined`.
    mockFetch.mockResolvedValue(new Response("{}", { status: 200 }));
    mockFetch.mockResolvedValueOnce(new Response("{}", { status: 401 }));

    render(
      <AuthProvider>
        <AuthStatusProbe />
      </AuthProvider>
    );

    expect(await screen.findByText("authenticated")).toBeInTheDocument();

    // Simulates apiClient.ts's tryRefresh giving up (idle timeout or a genuinely failed
    // refresh) -- AuthContext must react to this itself, not wait for a full remount.
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));

    expect(await screen.findByText("not authenticated")).toBeInTheDocument();
  });
});
