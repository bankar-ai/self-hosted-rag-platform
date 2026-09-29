import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IDLE_TIMEOUT_MS, recordActivity } from "./activityTracker";
import { apiFetch, SESSION_EXPIRED_EVENT } from "./apiClient";
import { getStoredUserId, setStoredUserId } from "./tokenStorage";

function setCsrfCookie(value: string): void {
  document.cookie = `csrf_token=${value}; path=/`;
}

describe("apiFetch", () => {
  beforeEach(() => {
    localStorage.clear();
    document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends credentials so the browser attaches the httpOnly auth cookies (ERP-116)", async () => {
    const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;
    mockFetch.mockResolvedValueOnce(new Response("{}", { status: 200 }));

    await apiFetch("/retrieval/query", { method: "POST" });

    const [, init] = mockFetch.mock.calls[0];
    expect(init.credentials).toBe("include");
  });

  it("attaches the CSRF header (read from the non-httpOnly csrf_token cookie) on a mutating request", async () => {
    setCsrfCookie("csrf-abc");
    const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;
    mockFetch.mockResolvedValueOnce(new Response("{}", { status: 200 }));

    await apiFetch("/generation/query", { method: "POST" });

    const [, init] = mockFetch.mock.calls[0];
    expect((init.headers as Headers).get("X-CSRF-Token")).toBe("csrf-abc");
  });

  it("does not attach a CSRF header on a GET (nothing to forge)", async () => {
    setCsrfCookie("csrf-abc");
    const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;
    mockFetch.mockResolvedValueOnce(new Response("{}", { status: 200 }));

    await apiFetch("/conversations");

    const [, init] = mockFetch.mock.calls[0];
    expect((init.headers as Headers).get("X-CSRF-Token")).toBeNull();
  });

  it("does not attempt a refresh when /auth/login itself returns 401 (ERP-116)", async () => {
    // A wrong-password login is a direct auth failure, not a stale-access-token situation a
    // refresh could fix -- and if this ever regresses, a login failure would silently trigger
    // a pointless second network call instead of surfacing the error immediately.
    const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;
    mockFetch.mockResolvedValueOnce(new Response("{}", { status: 401 }));

    const response = await apiFetch("/auth/login", { method: "POST" });

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(response.status).toBe(401);
  });

  it("refreshes once and retries on a 401, then succeeds", async () => {
    const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;
    mockFetch
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ user_id: "u1" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));

    const response = await apiFetch("/retrieval/query", { method: "POST" });

    expect(mockFetch).toHaveBeenCalledTimes(3);
    expect(response.status).toBe(200);
  });

  it("clears the stored user ID and fires SESSION_EXPIRED_EVENT when refresh also fails", async () => {
    setStoredUserId("u1");
    const onExpired = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);

    const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;
    mockFetch
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockResolvedValueOnce(new Response("{}", { status: 401 }));

    const response = await apiFetch("/retrieval/query", { method: "POST" });

    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(response.status).toBe(401);
    expect(getStoredUserId()).toBeNull();
    expect(onExpired).toHaveBeenCalledTimes(1);

    window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  });

  it("does not attempt a silent refresh once idle past the timeout, clearing the session instead (ERP-116)", async () => {
    setStoredUserId("u1");
    recordActivity();
    vi.useFakeTimers();
    vi.advanceTimersByTime(IDLE_TIMEOUT_MS + 1000);

    const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;
    mockFetch.mockResolvedValueOnce(new Response("{}", { status: 401 }));

    const response = await apiFetch("/retrieval/query", { method: "POST" });

    // Only the original 401 -- no /auth/refresh call was even attempted, since the session was
    // already idle-timed-out. A sliding refresh window would silently keep this alive forever.
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(response.status).toBe(401);
    expect(getStoredUserId()).toBeNull();

    vi.useRealTimers();
  });
});
