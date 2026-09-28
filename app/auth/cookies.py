"""httpOnly cookie delivery for access/refresh tokens, plus the CSRF double-submit cookie (ERP-116).

Replaces returning tokens in the JSON response body for the frontend to store in
`localStorage`, which was readable by any JS on the page (an XSS exposure).

`access_token`/`refresh_token` are `httpOnly` (invisible to JS, so an XSS bug can't read them).
`csrf_token` deliberately is not -- the frontend must read it and echo it back as an
`X-CSRF-Token` header on every mutating request; `app/auth/dependencies.py` verifies the header
matches the cookie. This is the standard double-submit pattern, and it's mandatory here (not
optional hardening) because `SameSite=None` -- required for the frontend/backend's cross-origin
deployment -- otherwise offers no CSRF protection at all on its own.
"""

import secrets

from fastapi import Response

from app.auth.config import AuthSettings

ACCESS_TOKEN_COOKIE = "access_token"
REFRESH_TOKEN_COOKIE = "refresh_token"
CSRF_TOKEN_COOKIE = "csrf_token"
CSRF_TOKEN_HEADER = "x-csrf-token"

# Refresh token only travels to the two endpoints that ever need it -- narrower exposure than
# sending it on every API call the way the access token cookie necessarily must.
_REFRESH_COOKIE_PATH = "/auth"


def _samesite(settings: AuthSettings) -> str:
    return "none" if settings.cookie_secure else "lax"


def set_auth_cookies(
    response: Response, access_token: str, refresh_token: str, settings: AuthSettings
) -> str:
    """Set the access/refresh/CSRF cookies on `response`. Returns the new CSRF token value."""
    samesite = _samesite(settings)
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        access_token,
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite=samesite,  # type: ignore[arg-type]
    )
    response.set_cookie(
        REFRESH_TOKEN_COOKIE,
        refresh_token,
        max_age=settings.refresh_token_expire_days * 86400,
        path=_REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=samesite,  # type: ignore[arg-type]
    )
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_TOKEN_COOKIE,
        csrf_token,
        max_age=settings.refresh_token_expire_days * 86400,
        path="/",
        httponly=False,
        secure=settings.cookie_secure,
        samesite=samesite,  # type: ignore[arg-type]
    )
    return csrf_token


def clear_auth_cookies(response: Response, settings: AuthSettings) -> None:
    """Clear all three auth cookies on `response` (logout).

    Passes the same `secure`/`samesite` attributes originally used to set each cookie -- some
    browsers won't clear a cookie via a `Max-Age=0` `Set-Cookie` whose attributes don't match
    the cookie actually stored.
    """
    samesite = _samesite(settings)
    response.delete_cookie(
        ACCESS_TOKEN_COOKIE, path="/", secure=settings.cookie_secure, samesite=samesite  # type: ignore[arg-type]
    )
    response.delete_cookie(
        REFRESH_TOKEN_COOKIE,
        path=_REFRESH_COOKIE_PATH,
        secure=settings.cookie_secure,
        samesite=samesite,  # type: ignore[arg-type]
    )
    response.delete_cookie(
        CSRF_TOKEN_COOKIE, path="/", secure=settings.cookie_secure, samesite=samesite  # type: ignore[arg-type]
    )
