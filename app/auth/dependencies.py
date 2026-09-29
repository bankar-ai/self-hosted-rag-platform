"""FastAPI dependencies for extracting and enforcing the authenticated caller."""

import hmac
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status

from app.auth.config import get_auth_settings
from app.auth.cookies import ACCESS_TOKEN_COOKIE, CSRF_TOKEN_COOKIE, CSRF_TOKEN_HEADER
from app.auth.schemas import CurrentUser
from app.auth.security import InvalidTokenError, decode_access_token
from app.core.rate_limit import get_default_rate_limiter

# ERP-116: methods that mutate state -- these are exactly the ones CSRF can forge (a forged GET
# has no side effect worth protecting against here), so only these require the CSRF header.
_CSRF_PROTECTED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def verify_csrf(request: Request) -> None:
    """Double-submit CSRF check (ERP-116): the `X-CSRF-Token` header must match the cookie.

    The cookie (non-httpOnly `csrf_token`) is readable by this frontend's own JS. A cross-site
    attacker can trigger a cookie-carrying request but cannot read the cookie's value (browser
    same-origin policy) to put it in the header themselves, so a mismatch means the request
    didn't originate from this frontend.
    """
    cookie_value = request.cookies.get(CSRF_TOKEN_COOKIE)
    header_value = request.headers.get(CSRF_TOKEN_HEADER)
    # Constant-time comparison, avoiding a subtle timing side-channel.
    if not cookie_value or not header_value or not hmac.compare_digest(cookie_value, header_value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing or invalid CSRF token")


def get_current_user(request: Request) -> CurrentUser:
    """Resolve and validate the caller's access token from the `access_token` cookie.

    (ERP-116 -- previously an `Authorization: Bearer` header sourced from `localStorage`.)
    Raises `HTTPException(401)` if the cookie is missing or the token is invalid/expired.
    For a state-changing request (`_CSRF_PROTECTED_METHODS`), also enforces the CSRF
    double-submit check before returning -- see `verify_csrf`.
    """
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        current_user = decode_access_token(token, get_auth_settings())
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        ) from exc
    if request.method in _CSRF_PROTECTED_METHODS:
        verify_csrf(request)
    return current_user


def rate_limited(scope: str) -> Callable[[CurrentUser], CurrentUser]:
    """Build a dependency requiring both a valid token and an unspent rate-limit budget (ERP-108).

    `scope` distinguishes independent counters per endpoint (e.g. `"generation"`,
    `"retrieval"`) sharing one `RATE_LIMIT_REQUESTS_PER_MINUTE` budget each, keyed by
    `current_user.id`. Raises `HTTPException(429)` with a `Retry-After` header once the
    caller's per-minute budget for `scope` is spent.
    """

    def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        allowed, retry_after = get_default_rate_limiter().check(current_user.id, scope)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded, please slow down",
                headers={"Retry-After": str(retry_after)},
            )
        return current_user

    return _check


def require_role(role: str) -> Callable[[CurrentUser], CurrentUser]:
    """Build a dependency that additionally requires `current_user.role == role`.

    Raises `HTTPException(403)` if the caller's role doesn't match. Unused by any endpoint
    in this ticket (no admin-only endpoints ship yet — see the spec's deferred follow-ups)
    but provided so a future admin endpoint can depend on it directly.
    """

    def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role != role:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return current_user

    return _check
