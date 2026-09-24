import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../lib/AuthContext";
import { setTokens } from "../lib/tokenStorage";
import IntroModal from "./IntroModal";

function renderAuthenticated() {
  setTokens({ accessToken: "a", refreshToken: "b" });
  return render(
    <MemoryRouter>
      <AuthProvider>
        <IntroModal />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe("IntroModal", () => {
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
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("shows on first render when authenticated and the localStorage flag is unset", () => {
    renderAuthenticated();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("does not show when the localStorage flag is already set", () => {
    localStorage.setItem("introSeen_v1", "true");
    renderAuthenticated();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not render when unauthenticated, even with the localStorage flag unset", () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <IntroModal />
        </AuthProvider>
      </MemoryRouter>
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("dismisses and sets the flag when closed", async () => {
    renderAuthenticated();
    await userEvent.click(screen.getByRole("button", { name: /got it/i }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(localStorage.getItem("introSeen_v1")).toBe("true");
  });
});
