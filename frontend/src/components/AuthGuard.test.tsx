import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../lib/AuthContext";
import { setTokens } from "../lib/tokenStorage";
import AuthGuard from "./AuthGuard";

describe("AuthGuard", () => {
  beforeEach(() => {
    localStorage.clear();
    // AuthProvider fetches /auth/me on mount whenever tokens already exist -- stub it so that
    // fire-and-forget call doesn't hit the real network during tests.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ id: "u1", email: "user@example.com", role: "user", is_active: true }),
          { status: 200 }
        )
      )
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("redirects to /login when not authenticated", () => {
    render(
      <MemoryRouter initialEntries={["/chat"]}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<p>Login page</p>} />
            <Route
              path="/chat"
              element={
                <AuthGuard>
                  <p>Chat page</p>
                </AuthGuard>
              }
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    expect(screen.getByText("Login page")).toBeInTheDocument();
  });

  it("renders the protected content when authenticated", () => {
    setTokens({ accessToken: "a", refreshToken: "b" });

    render(
      <MemoryRouter initialEntries={["/chat"]}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<p>Login page</p>} />
            <Route
              path="/chat"
              element={
                <AuthGuard>
                  <p>Chat page</p>
                </AuthGuard>
              }
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    expect(screen.getByText("Chat page")).toBeInTheDocument();
  });
});
