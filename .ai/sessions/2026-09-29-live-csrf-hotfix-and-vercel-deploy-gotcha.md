# Session — live CSRF hotfix and Vercel deploy gotcha

Date: 2026-09-29
Tickets Touched: ERP-116

## Decisions

- Treat a real-user 403 report as an urgent live incident, diagnose against the real production
  URLs (not localhost), and fix/deploy same-session rather than filing a ticket for later.
- When curl-based "live verification" and real browser behavior disagree, trust the browser
  constraint (`document.cookie` is strictly same-origin) over a curl check that can't reproduce
  it — curl's cookie jar has no page-origin restriction, so it had been masking this bug.
- When a `main` push shows no Vercel deployment after several minutes of polling, don't keep
  waiting — force a fresh webhook delivery with a trivial `--allow-empty` commit rather than
  digging into Vercel/GitHub integration internals under live-outage time pressure.

## Implementation Summary

Root cause: ERP-116's cookie-based CSRF double-submit pattern set the `csrf_token` cookie from
the API's origin (the VM). The frontend (Vercel, a different origin) tried to read it via
`document.cookie` to build the `X-CSRF-Token` header — but `document.cookie` only ever exposes
cookies belonging to the *current page's own origin*, so the cookie was permanently invisible to
that JS regardless of `SameSite`/`credentials` settings. Every real generated request 403'd.

Fix (backend + frontend, deployed together):
- `app/auth/router.py`: `login`/`refresh`/`oidc_callback` now return `csrf_token` in the JSON
  body (`AuthActionResponse`, `app/auth/schemas.py`) alongside the existing cookies — a
  same-origin-safe channel since the frontend is just reading its own fetch response.
- `frontend/src/lib/tokenStorage.ts`: caches the CSRF token client-side
  (`getStoredCsrfToken`/`setStoredCsrfToken`/`clearStoredCsrfToken`), alongside the existing
  user-ID marker.
- `frontend/src/lib/apiClient.ts`: `withCsrfHeader` reads the cached token instead of parsing
  `document.cookie`; `tryRefresh()` updates the cached token from each refresh response;
  `apiFetch`/`uploadWithProgress` retry on `403` as well as `401`, so a session already broken by
  this bug self-heals via one `/auth/refresh` call instead of forcing every user to log in again.
- `tests/auth/test_router.py`: new regression test asserting the response body's `csrf_token`
  matches the cookie's value — the exact case no prior test caught, because `TestClient`'s cookie
  jar is client-scoped and origin-unrestricted, unlike a real browser.

Shipped via PR #92 (develop) → PR #93 (develop→main HOTFIX, merge commit `6fc6aa1`). Backend
redeployed via `deploy_vm.bat`, confirmed running `6fc6aa1`.

Separate issue hit during the same incident: Vercel's GitHub webhook never fired for the
`6fc6aa1` push. `gh api repos/.../commits/6fc6aa1/status` and `.../deployments?sha=6fc6aa1` both
stayed empty for 6+ minutes, and the Vercel dashboard's Deployments list had no entry for that
commit at all (every other push, on any branch, had built within seconds). Confirmed via a direct
curl of the live JS bundle that the old pre-fix build was still being served. Worked around with
`git commit --allow-empty -m "..." && git push origin main` (commit `4df29fe`) — this got a fresh
push event, and `commits/4df29fe/status` showed a successful Vercel deployment shortly after.

## Blockers

None remaining. One throwaway verification user (`verify-1790657242@example.com`) is still in
production Postgres — deleting it via `DELETE /admin/users/{id}` was blocked by this
environment's permission classifier (inline use of the admin password); harmless, delete
opportunistically later via the admin panel.

## Next Steps

- Investigate *why* the Vercel webhook silently dropped the `6fc6aa1` push (GitHub's
  webhook-delivery log for the Vercel App installation, org settings, would show whether GitHub
  never sent it or Vercel's endpoint failed to ack it) — not urgent now that the workaround is
  documented, but worth root-causing if it recurs.
- Resume the evaluation-mechanism question the user raised before this incident interrupted it
  (golden-dataset harness vs. production-traffic sampling, ERP-083/ERP-097) — deferred, not
  forgotten.
- Delete the leftover throwaway verification user via the admin UI/CLI outside this session's
  permission constraints.
