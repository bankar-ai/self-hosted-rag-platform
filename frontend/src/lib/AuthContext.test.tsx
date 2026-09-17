import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "./AuthContext";
import { setTokens } from "./tokenStorage";

function UserEmailProbe() {
  const { user } = useAuth();
  return <p>{user ? user.email : "no user yet"}</p>;
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
    setTokens({ accessToken: "a", refreshToken: "b" });
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
});
