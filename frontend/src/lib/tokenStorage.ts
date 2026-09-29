// ERP-116: access/refresh tokens moved to httpOnly cookies (invisible to this file's JS,
// by design -- that's what closes the XSS exposure the previous localStorage-based version
// had). All that's left to store client-side is the caller's own user ID: a non-sensitive
// marker the app still needs synchronously (to scope other localStorage keys by user, and to
// render an optimistic "logged in" UI state before the first `/auth/me` round-trip resolves).

const STORAGE_KEY = "rag-user-id";

export function getStoredUserId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setStoredUserId(userId: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, userId);
  } catch {
    // best-effort only
  }
}

export function clearStoredUserId(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // best-effort only
  }
}
