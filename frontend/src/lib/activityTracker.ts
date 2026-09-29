// ERP-116: idle-timeout tracking. `apiFetch`'s silent-refresh-on-401 flow (`apiClient.ts`)
// consults this before refreshing -- a session that's been idle past the threshold is treated
// as expired rather than silently kept alive forever, closing the "no timed logout" gap a
// sliding refresh-token window otherwise leaves open.

const LAST_ACTIVITY_KEY = "rag-last-activity";
// Matches AuthSettings.access_token_expire_minutes (30 min, app/auth/config.py) -- if the user
// hasn't interacted with the page for longer than one access-token lifetime, treat the session
// as abandoned rather than silently keeping it alive on their behalf.
export const IDLE_TIMEOUT_MS = 30 * 60 * 1000;

/** Reads/writes are best-effort -- a private-browsing/storage-blocked browser just falls back
 * to never idle-timing-out (the pre-existing sliding-refresh behavior), same degrade-gracefully
 * pattern already used elsewhere in this file's siblings (e.g. `tokenStorage.ts`). */
export function recordActivity(): void {
  try {
    localStorage.setItem(LAST_ACTIVITY_KEY, String(Date.now()));
  } catch {
    // best-effort only
  }
}

function getLastActivityAt(): number | null {
  try {
    const raw = localStorage.getItem(LAST_ACTIVITY_KEY);
    return raw ? Number(raw) : null;
  } catch {
    return null;
  }
}

/** `false` (never idle-timed-out) if no activity has been recorded yet -- a page that never
 * calls `startActivityTracking` (e.g. the login page itself) must not accidentally look idle. */
export function isIdleTimedOut(): boolean {
  const lastActivityAt = getLastActivityAt();
  if (lastActivityAt === null) return false;
  return Date.now() - lastActivityAt > IDLE_TIMEOUT_MS;
}

let tracking = false;

/** Registers a one-time, throttled global listener for real user interaction (click/keydown) --
 * safe to call multiple times (e.g. once per `AuthProvider` mount); only attaches once per page
 * load. Also records activity immediately, so a fresh login/page-load never starts "idle." */
export function startActivityTracking(): void {
  recordActivity();
  if (tracking) return;
  tracking = true;
  const listener = () => recordActivity();
  window.addEventListener("click", listener);
  window.addEventListener("keydown", listener);
}
