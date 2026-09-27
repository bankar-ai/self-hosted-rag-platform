"""FastAPI dependencies for extracting and enforcing the authenticated caller."""

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.auth.config import get_auth_settings
from app.auth.schemas import CurrentUser
from app.auth.security import InvalidTokenError, decode_access_token
from app.core.rate_limit import get_default_rate_limiter

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(token: str = Depends(_oauth2_scheme)) -> CurrentUser:
    """Resolve and validate the caller's access token. Raises `HTTPException(401)` if invalid."""
    try:
        return decode_access_token(token, get_auth_settings())
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        ) from exc


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
