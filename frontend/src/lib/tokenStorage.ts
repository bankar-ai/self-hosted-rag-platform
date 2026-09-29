// ERP-116: access/refresh tokens moved to httpOnly cookies (invisible to this file's JS,
// by design -- that's what closes the XSS exposure the previous localStorage-based version
// had). What's left to store client-side is:
// - the caller's own user ID: a non-sensitive marker the app still needs synchronously (to
//   scope other localStorage keys by user, and to render an optimistic "logged in" UI state
//   before the first `/auth/me` round-trip resolves).
// - the CSRF token: NOT read from its own cookie (a real bug found live, 2026-09-29 -- the
//   `csrf_token` cookie is set by the API's origin, and `document.cookie` can only ever expose
//   cookies belonging to the *current page's own origin*; since the frontend and API are
//   different origins, that cookie is permanently invisible to this file's JS no matter what
//   `SameSite`/`credentials` settings are used on the fetch itself). Instead, `/auth/login`/
//   `/refresh` return the same value in their JSON response body -- a channel this file *can*
//   read cross-origin, since it's just reading its own fetch response -- and this module caches
//   it here so it survives a page reload the same way the user ID marker does.

const USER_ID_KEY = "rag-user-id";
const CSRF_TOKEN_KEY = "rag-csrf-token";

export function getStoredUserId(): string | null {
  try {
    return localStorage.getItem(USER_ID_KEY);
  } catch {
    return null;
  }
}

export function setStoredUserId(userId: string): void {
  try {
    localStorage.setItem(USER_ID_KEY, userId);
  } catch {
    // best-effort only
  }
}

export function clearStoredUserId(): void {
  try {
    localStorage.removeItem(USER_ID_KEY);
  } catch {
    // best-effort only
  }
}

export function getStoredCsrfToken(): string | null {
  try {
    return localStorage.getItem(CSRF_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setStoredCsrfToken(csrfToken: string): void {
  try {
    localStorage.setItem(CSRF_TOKEN_KEY, csrfToken);
  } catch {
    // best-effort only
  }
}

export function clearStoredCsrfToken(): void {
  try {
    localStorage.removeItem(CSRF_TOKEN_KEY);
  } catch {
    // best-effort only
  }
}
