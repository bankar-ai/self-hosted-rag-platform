"""Auth API: registration, login, refresh-token rotation, and logout."""

import uuid
from typing import cast

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse

from app.auth.dependencies import require_role
from app.auth.oidc import InvalidOidcStateError, OidcTokenExchangeError, OidcTokenValidationError
from app.auth.schemas import (
    CurrentUser,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    Role,
    TokenResponse,
    UpdateUserActiveRequest,
    UserResponse,
)
from app.auth.service import (
    AccountDisabledError,
    CannotDeleteSelfError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    OidcAccountConflictError,
    OidcNotConfiguredError,
    UserNotFoundError,
    complete_oidc_login,
    delete_user,
    list_all_users,
    refresh_access_token,
    register_user,
    revoke_user_sessions,
    set_user_active_status,
    start_oidc_login,
)
from app.auth.service import login as login_user
from app.auth.service import logout as logout_user

router = APIRouter(prefix="/auth", tags=["auth"])
oidc_router = APIRouter(prefix="/auth/oidc", tags=["auth"])

# HttpOnly/Secure/SameSite=Lax and scoped to this router's own path -- carries the PKCE verifier
# and nonce out of any URL entirely, and binds a callback to the browser that started the flow
# (login-CSRF defense). See app/auth/oidc.py's create_oidc_cookie/decode_oidc_cookie.
_OIDC_STATE_COOKIE = "oidc_state"
_OIDC_COOKIE_PATH = "/auth/oidc"
_require_admin = require_role("admin")
admin_router = APIRouter(prefix="/admin/users", tags=["admin"], dependencies=[Depends(_require_admin)])


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest) -> UserResponse:
    """Register a new user with the `user` role."""
    try:
        user = register_user(request.email, request.password)
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    return UserResponse(id=user.id, email=user.email, role=cast(Role, user.role), is_active=user.is_active)


@router.post("/login")
def login(request: LoginRequest) -> TokenResponse:
    """Exchange email + password for an access + refresh token pair."""
    try:
        return login_user(request.email, request.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        ) from exc
    except AccountDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled"
        ) from exc


@router.post("/refresh")
def refresh(request: RefreshRequest) -> TokenResponse:
    """Rotate a refresh token for a new access + refresh token pair."""
    try:
        return refresh_access_token(request.refresh_token)
    except InvalidRefreshTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        ) from exc
    except AccountDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled"
        ) from exc


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: LogoutRequest) -> None:
    """Revoke a refresh token."""
    logout_user(request.refresh_token)


@oidc_router.get("/{provider}/login")
def oidc_login(provider: str) -> RedirectResponse:
    """Redirect to `provider`'s OIDC authorization endpoint to start login (Authorization Code + PKCE)."""
    try:
        login_start = start_oidc_login(provider)
    except OidcNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown OIDC provider"
        ) from exc
    response = RedirectResponse(
        login_start.authorization_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )
    response.set_cookie(
        _OIDC_STATE_COOKIE,
        login_start.state_cookie_value,
        max_age=login_start.state_cookie_max_age_seconds,
        path=_OIDC_COOKIE_PATH,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@oidc_router.get("/{provider}/callback")
def oidc_callback(
    provider: str,
    code: str,
    state: str,
    response: Response,
    oidc_state_cookie: str | None = Cookie(default=None, alias=_OIDC_STATE_COOKIE),
) -> TokenResponse:
    """Complete an OIDC login: exchange `code`, verify the ID token, and issue tokens.

    Returns the same `access_token`/`refresh_token` pair `POST /auth/login` returns — this is an
    API-only backend with no frontend to redirect back to. The `oidc_state` cookie set by
    `GET /auth/oidc/{provider}/login` is required; it's cleared here on success (FastAPI drops
    `Response` header mutations made before a raised `HTTPException`, so it can't also be cleared
    on the error paths below -- those rely on the cookie's own short `Max-Age` instead, which is
    no weaker: the underlying authorization `code` is single-use at the IdP regardless).
    """
    try:
        tokens = complete_oidc_login(provider, code, state, oidc_state_cookie)
    except OidcNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown OIDC provider"
        ) from exc
    except InvalidOidcStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OIDC state"
        ) from exc
    except (OidcTokenExchangeError, OidcTokenValidationError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC login failed") from exc
    except OidcAccountConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists and could not be automatically linked",
        ) from exc
    except AccountDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled"
        ) from exc
    response.delete_cookie(_OIDC_STATE_COOKIE, path=_OIDC_COOKIE_PATH)
    return tokens


@admin_router.get("")
def list_users_endpoint() -> list[UserResponse]:
    """List every registered user. Requires the `admin` role."""
    return [
        UserResponse(id=user.id, email=user.email, role=cast(Role, user.role), is_active=user.is_active)
        for user in list_all_users()
    ]


@admin_router.patch("/{user_id}")
def update_user_active(user_id: uuid.UUID, request: UpdateUserActiveRequest) -> UserResponse:
    """Enable or disable a user's account. Requires the `admin` role."""
    try:
        user = set_user_active_status(user_id, request.is_active)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from exc
    return UserResponse(id=user.id, email=user.email, role=cast(Role, user.role), is_active=user.is_active)


@admin_router.post("/{user_id}/revoke-sessions", status_code=status.HTTP_204_NO_CONTENT)
def revoke_sessions(user_id: uuid.UUID) -> None:
    """Revoke every active refresh token for a user, forcing re-authentication. Requires the `admin` role."""
    try:
        revoke_user_sessions(user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from exc


@admin_router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_endpoint(
    user_id: uuid.UUID, current_user: CurrentUser = Depends(_require_admin)
) -> None:
    """Permanently delete a user and everything they own. Requires the `admin` role. Irreversible."""
    try:
        delete_user(user_id, current_user.id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from exc
    except CannotDeleteSelfError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Cannot delete your own account"
        ) from exc
