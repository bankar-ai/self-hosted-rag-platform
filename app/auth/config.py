"""Authentication settings, loaded from environment variables.

`jwt_secret_key` has no default — it must come from `AUTH_JWT_SECRET_KEY` — since a
hardcoded signing secret would let anyone forge access tokens (see CLAUDE.md's
never-hardcode-secrets rule).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    """Configuration for JWT signing, token lifetimes, and the revocation cache.

    Overridable via `AUTH_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="AUTH_")

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    redis_url: str = "redis://localhost:6379/0"
    redis_socket_timeout_seconds: float = 2.0

    # ERP-116: httpOnly cookie delivery for access/refresh/CSRF tokens, replacing the previous
    # localStorage + Authorization-header flow (XSS-exposed). `cookie_secure=True` (the default,
    # required for production) sets `Secure` + `SameSite=None` -- `SameSite=None` is required
    # because the frontend (Vercel) and this API (the VM) are different registrable domains, so
    # every real cross-origin request needs it just to have the cookie attached at all; that in
    # turn is exactly what makes CSRF protection (the X-CSRF-Token double-submit check in
    # app/auth/dependencies.py) mandatory here, not optional. `Secure` cookies require HTTPS,
    # which plain local dev (http://localhost) doesn't have -- set `AUTH_COOKIE_SECURE=false`
    # for local dev only, which switches to `Secure=False` + `SameSite=Lax` (sufficient since
    # local frontend/backend are same-site, just different ports).
    cookie_secure: bool = True

    # OIDC (ERP-032). All four of issuer/client_id/client_secret/redirect_uri must be set for
    # OIDC login to be considered "configured" -- see app/auth/service.py's `_is_oidc_configured`.
    # Left unset by default so a deployment that never configures OIDC sees no behavior change
    # (both /auth/oidc/* endpoints 404). Provider-agnostic: only `oidc_provider_name` (the route
    # segment) is provider-specific; everything else (endpoints, signing keys) comes from the
    # issuer's discovery document at request time, never hardcoded for a specific IdP.
    oidc_provider_name: str = "google"
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    oidc_state_expire_seconds: int = 300
    oidc_http_timeout_seconds: float = 5.0


@lru_cache
def get_auth_settings() -> AuthSettings:
    """Return the process-wide cached `AuthSettings` instance."""
    return AuthSettings()  # type: ignore[call-arg]
