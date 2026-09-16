import type { TokenResponse } from "./types";
import { clearTokens, getTokens, setTokens } from "./tokenStorage";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL as string;

async function rawFetch(path: string, init: RequestInit): Promise<Response> {
  const tokens = getTokens();
  const headers = new Headers(init.headers);
  if (tokens) {
    headers.set("Authorization", `Bearer ${tokens.accessToken}`);
  }
  return fetch(`${API_BASE_URL}${path}`, { ...init, headers });
}

async function tryRefresh(): Promise<boolean> {
  const tokens = getTokens();
  if (!tokens) return false;

  const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: tokens.refreshToken }),
  });
  if (!response.ok) {
    clearTokens();
    return false;
  }

  const newTokens = (await response.json()) as TokenResponse;
  setTokens({ accessToken: newTokens.access_token, refreshToken: newTokens.refresh_token });
  return true;
}

/**
 * Call the backend, attaching the stored access token. On a 401, refreshes the token pair
 * once and retries the original request; if refresh also fails, tokens are cleared and the
 * 401 response is returned as-is for the caller to handle (redirect to /login).
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const first = await rawFetch(path, init);
  if (first.status !== 401) return first;

  const refreshed = await tryRefresh();
  if (!refreshed) return first;

  return rawFetch(path, init);
}
