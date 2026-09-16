import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import { AuthProvider } from "../lib/AuthContext";
import { setTokens } from "../lib/tokenStorage";
import AuthGuard from "./AuthGuard";

describe("AuthGuard", () => {
  beforeEach(() => {
    localStorage.clear();
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
