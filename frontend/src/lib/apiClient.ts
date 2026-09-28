import { isIdleTimedOut } from "./activityTracker";
import { clearStoredUserId } from "./tokenStorage";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL as string;

// ERP-116: fired whenever `tryRefresh` gives up on the session (idle-timeout or a genuinely
// failed refresh) -- `AuthContext` listens for this to react immediately, rather than only
// re-checking auth state on the next full page load/remount.
export const SESSION_EXPIRED_EVENT = "auth:session-expired";

const CSRF_COOKIE = "csrf_token";
const CSRF_HEADER = "X-CSRF-Token";
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
// The token-issuing endpoints themselves -- excluded from apiFetch's refresh-and-retry dance
// below. `/auth/me` is deliberately NOT here: it's an ordinary protected endpoint that should
// benefit from a silent refresh-and-retry on an expired access token same as any other.
const AUTH_TOKEN_ENDPOINTS = new Set(["/auth/login", "/auth/register", "/auth/refresh"]);

/** Reads `csrf_token` straight from `document.cookie` -- deliberately not `httpOnly` (unlike
 * the access/refresh token cookies) specifically so this can read it fresh before every
 * mutating request, matching the backend's double-submit CSRF check. */
function readCsrfToken(): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${CSRF_COOKIE}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

function withCsrfHeader(method: string, headers: Headers): Headers {
  if (!MUTATING_METHODS.has(method.toUpperCase())) return headers;
  const csrfToken = readCsrfToken();
  if (csrfToken) headers.set(CSRF_HEADER, csrfToken);
  return headers;
}

async function rawFetch(path: string, init: RequestInit): Promise<Response> {
  const headers = withCsrfHeader(init.method ?? "GET", new Headers(init.headers));
  // ERP-116: the browser attaches the httpOnly access/refresh cookies automatically -- but
  // only if told to send credentials on this cross-origin request (frontend and API are
  // different origins) via `credentials: "include"`.
  return fetch(`${API_BASE_URL}${path}`, { ...init, headers, credentials: "include" });
}

function expireSession(): void {
  clearStoredUserId();
  window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
}

async function tryRefresh(): Promise<boolean> {
  // ERP-116: an idle session is not silently kept alive just because the browser tab is still
  // open -- if nobody has interacted with the page in a while, treat the access-token expiry as
  // a real logout rather than an invisible background refresh.
  if (isIdleTimedOut()) {
    expireSession();
    return false;
  }

  const response = await rawFetch("/auth/refresh", { method: "POST" });
  if (!response.ok) {
    expireSession();
    return false;
  }
  return true;
}

/**
 * Call the backend, sending the httpOnly auth cookies automatically. On a 401, refreshes the
 * token pair once and retries the original request; if refresh also fails, the session is
 * expired (see `expireSession`) and the 401 response is returned as-is for the caller to handle.
 *
 * Never attempts this dance for the token-issuing endpoints themselves (`AUTH_TOKEN_ENDPOINTS`,
 * ERP-116) -- a 401 from `/auth/login`/`/register`/`/refresh` is a direct, meaningful auth
 * failure (wrong password, an already-invalid refresh token, ...), not a stale-access-token
 * situation a refresh could fix. Previously this was implicit (no stored token yet meant
 * `tryRefresh` short-circuited before ever calling the network); now that tokens are invisible
 * httpOnly cookies, that check no longer exists client-side, so it has to be explicit here.
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const first = await rawFetch(path, init);
  if (first.status !== 401 || AUTH_TOKEN_ENDPOINTS.has(path)) return first;

  const refreshed = await tryRefresh();
  if (!refreshed) return first;

  return rawFetch(path, init);
}

interface UploadResult {
  ok: boolean;
  status: number;
  body: unknown;
}

function rawUpload(path: string, file: File, onProgress: (fraction: number) => void): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE_URL}${path}`);
    xhr.withCredentials = true;
    const csrfToken = readCsrfToken();
    if (csrfToken) xhr.setRequestHeader(CSRF_HEADER, csrfToken);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        body = null;
      }
      resolve({ ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status, body });
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    const formData = new FormData();
    formData.append("file", file);
    xhr.send(formData);
  });
}

/**
 * Upload `file` to `path` via `XMLHttpRequest` (not `fetch`, which has no reliable
 * upload-progress event), reporting transfer progress as a 0-1 fraction via `onProgress`.
 * Mirrors `apiFetch`'s 401-refresh-and-retry behavior once, for parity with ordinary requests.
 */
export async function uploadWithProgress(
  path: string,
  file: File,
  onProgress: (fraction: number) => void
): Promise<UploadResult> {
  const first = await rawUpload(path, file, onProgress);
  if (first.status !== 401) return first;

  const refreshed = await tryRefresh();
  if (!refreshed) return first;

  return rawUpload(path, file, onProgress);
}
