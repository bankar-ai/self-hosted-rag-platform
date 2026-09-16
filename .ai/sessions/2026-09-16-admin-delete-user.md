# Session — Admin Delete-User Endpoint (ERP-040)

Date: 2026-09-16
Tickets Touched: ERP-040

## Decisions

- Hard delete, not soft delete — this is a cleanup tool for stray test/demo accounts (the gap
  named at the end of the previous Grafana Cloud dashboard session), not a data-retention feature.
- Blocked an admin from deleting their own account (409) — a small, deliberate addition beyond the
  literal "clean up test users" ask, since an admin locking themselves out is an easy footgun for
  a genuinely irreversible action.
- Placed the cross-table deletion logic in `app/auth/repository.py` (importing
  `app.generation.models`/`app.ingestion.models`), mirroring the existing precedent in
  `app.evaluation.repository.cleanup_eval_data`, which already does the same kind of cross-domain
  cleanup for eval-run data.

## Implementation Summary

- `app/auth/repository.py`: new `delete_user_and_owned_data(session, user_id)` — deletes
  conversation messages, conversations, chunks, documents, refresh tokens, and OIDC identities (in
  that FK-safe order) before the user row itself. Uses SQLAlchemy 2.0 `delete()` statements,
  matching this file's existing `update()` usage style (not the legacy `Query.delete()` style
  `cleanup_eval_data` uses).
- `app/auth/service.py`: new `delete_user(user_id, acting_admin_id)` (raises
  `CannotDeleteSelfError`/`UserNotFoundError`) and `_delete_owner_faiss_index` (best-effort,
  swallows `OSError`, matching every other observability/cache integration's
  never-load-bearing-if-not-critical philosophy in this repo).
- `app/auth/router.py`: `DELETE /admin/users/{user_id}` on the existing `admin_router`. Needed the
  caller's own ID for the self-delete check, so extracted `_require_admin = require_role("admin")`
  as a module-level singleton (ruff's B008 blocks calling `require_role("admin")` directly in a
  parameter default).
- Tests: `tests/auth/test_admin.py` (non-admin 403, unknown-user 404, self-delete 409, and a full
  delete exercising every table via real owned data — a document/chunk and a conversation/message,
  not just an empty case), `tests/auth/test_service.py` (unit-level equivalents plus the FAISS
  deletion-failure degrade-gracefully path), `tests/test_auth_required.py` (added to the
  401-without-token sweep). Reached 100% coverage on the new `repository.py` code, 99% on
  `service.py` (one pre-existing unrelated line).
- Committed to `develop` (`ca768f9`) and pushed; deployed to the live VM (`git pull` + `uv sync` +
  `systemctl restart`).

## Live Verification

The live deployment had **no bootstrapped admin account at all** — ERP-027 shipped the admin
endpoints in the first place, but never created one; the pre-existing migration-seeded
`system@internal` user has an intentionally-invalid password hash and can't log in. Created a real
first admin (`ops-admin@self-hosted-rag-platform.internal`) directly via the repository layer on
the VM (a one-off script, run via SSH with the user's explicit approval since the environment's
permission classifier flagged the script for reading `.env` secrets and printing a token/password
over SSH — reasonable caution, resolved by asking rather than working around it). Used that admin's
token to call the live `DELETE /admin/users/{id}` endpoint against all three throwaway test users
left behind by ERP-038/ERP-039/the Grafana dashboard build. All three returned `204`;
`GET /admin/users` afterward confirmed only the two legitimate admin accounts remain.

New credential (`ops-admin@self-hosted-rag-platform.internal` + password) stored in
`C:\Users\Pankaj\.credentials\self-hosted-rag-platform-credentials.md`; non-secret operational note
added to `D:\github-projects\gcp-deployment-tracker.md`.

## Blockers

None — ERP-040 is fully resolved.

## Next Steps

- No immediate follow-up. The live deployment now has a genuine ops admin account for future
  cleanup/management tasks, not just this one-off use.
