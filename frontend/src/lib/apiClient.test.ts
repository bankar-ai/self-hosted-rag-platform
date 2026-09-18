import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "./apiClient";
import { clearTokens, setTokens } from "./tokenStorage";

describe("apiFetch", () => {
  beforeEach(() => {
    clearTokens();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("attaches the Authorization header when a token is stored", async () => {
    setTokens({ accessToken: "token-123", refreshToken: "refresh-123" });
    const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;
    mockFetch.mockResolvedValueOnce(new Response("{}", { status: 200 }));

    await apiFetch("/retrieval/query", { method: "POST" });

    const [, init] = mockFetch.mock.calls[0];
    expect((init.headers as Headers).get("Authorization")).toBe("Bearer token-123");
  });

  it("refreshes once and retries on a 401, then succeeds", async () => {
    setTokens({ accessToken: "expired", refreshToken: "refresh-123" });
    const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;
    mockFetch
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ access_token: "new-token", refresh_token: "new-refresh", token_type: "bearer" }),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));

    const response = await apiFetch("/retrieval/query", { method: "POST" });

    expect(mockFetch).toHaveBeenCalledTimes(3);
    expect(response.status).toBe(200);
  });

  it("clears tokens and does not loop when refresh also fails", async () => {
    setTokens({ accessToken: "expired", refreshToken: "also-expired" });
    const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;
    mockFetch
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockResolvedValueOnce(new Response("{}", { status: 401 }));

    const response = await apiFetch("/retrieval/query", { method: "POST" });

    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(response.status).toBe(401);
  });
});
