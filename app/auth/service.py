"""Business logic for registration, login, refresh-token rotation, logout, and OIDC login."""

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple, cast

from app.auth import oidc
from app.auth.cache import RevocationCache, get_default_revocation_cache
from app.auth.config import AuthSettings, get_auth_settings
from app.auth.models import UserRecord
from app.auth.repository import (
    create_oidc_identity,
    create_oidc_user,
    create_refresh_token,
    create_user,
    delete_user_and_owned_data,
    get_oidc_identity,
    get_refresh_token_by_hash,
    get_user_by_email,
    get_user_by_id,
    list_users,
    revoke_all_refresh_tokens_for_user,
    revoke_refresh_token,
    set_user_active,
)
from app.auth.schemas import TokenResponse
from app.auth.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.core.db import get_session_factory
from app.embedding.config import get_embedding_settings
from app.embedding.index import OwnerFaissIndexStore

logger = logging.getLogger(__name__)


class EmailAlreadyRegisteredError(Exception):
    """Raised when registering with an email that's already taken."""


class InvalidCredentialsError(Exception):
    """Raised when login credentials don't match any user."""


class InvalidRefreshTokenError(Exception):
    """Raised when a presented refresh token is missing, expired, or revoked."""


class AccountDisabledError(Exception):
    """Raised when a login or refresh is attempted for a disabled user."""


class UserNotFoundError(Exception):
    """Raised when an admin operation targets an unknown user id."""


class CannotDeleteSelfError(Exception):
    """Raised when an admin attempts to delete their own account."""


class OidcNotConfiguredError(Exception):
    """Raised when OIDC login is requested for an unconfigured or unknown provider.

    Deliberately raised identically for "OIDC isn't configured at all" and "this provider name
    doesn't match the configured one" -- both become the same generic 404, so an unauthenticated
    caller can't use the response to probe which providers (if any) are configured.
    """


class OidcAccountConflictError(Exception):
    """Raised when an OIDC login's email matches an existing account that can't be auto-linked.

    See the design spec's Account Linking Decision: auto-linking only happens when the IdP's ID
    token asserts `email_verified: true` for the claimed email. Otherwise, silently linking (or
    creating a second account for) that email would let anyone who controls an OIDC identity with
    an unverified claim to someone else's address take over the existing account for that
    address -- exactly the account-takeover risk this decision exists to close off.
    """


def _as_aware_utc(value: datetime) -> datetime:
    """Attach UTC tzinfo to a naive `datetime` (the `expires_at` column is stored without one)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def register_user(email: str, password: str) -> UserRecord:
    """Register a new user with the `user` role. Raises `EmailAlreadyRegisteredError` on a duplicate."""
    session_factory = get_session_factory()
    with session_factory() as session:
        if get_user_by_email(session, email) is not None:
            raise EmailAlreadyRegisteredError(email)
        user = create_user(session, email, hash_password(password))
        session.commit()
        return user


def _issue_tokens(user: UserRecord, settings: AuthSettings) -> TokenResponse:
    session_factory = get_session_factory()
    access_token = create_access_token(user.id, user.role, settings)
    raw_refresh_token = generate_refresh_token()
    token_hash = hash_refresh_token(raw_refresh_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    with session_factory() as session:
        create_refresh_token(session, user.id, token_hash, expires_at)
        session.commit()
    return TokenResponse(access_token=access_token, refresh_token=raw_refresh_token)


def login(
    email: str, password: str, settings: AuthSettings | None = None
) -> TokenResponse:
    """Exchange email + password for an access + refresh token pair.

    Raises `InvalidCredentialsError` for an unknown email, a wrong password, or an OIDC-only
    account with no local password set (`hashed_password is None`) — all three deliberately the
    same error, to avoid confirming which emails are registered or how a given account
    authenticates. Raises `AccountDisabledError` if the password matched but the account is
    disabled — safe to distinguish here since the password was already verified, so it reveals
    nothing new about which emails are registered.
    """
    settings = settings or get_auth_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        user = get_user_by_email(session, email)
        if user is None or user.hashed_password is None or not verify_password(
            password, user.hashed_password
        ):
            raise InvalidCredentialsError
        if not user.is_active:
            raise AccountDisabledError
    return _issue_tokens(user, settings)


def refresh_access_token(
    raw_refresh_token: str,
    settings: AuthSettings | None = None,
    revocation_cache: RevocationCache | None = None,
) -> TokenResponse:
    """Rotate `raw_refresh_token` for a new access + refresh token pair.

    Checks the revocation cache first for a fast rejection of a known-revoked token,
    then falls back to the authoritative Postgres check (missing, revoked, or expired
    all raise `InvalidRefreshTokenError`). Rotation revokes the presented token and
    caches that revocation with a TTL matching its remaining natural validity.
    """
    settings = settings or get_auth_settings()
    revocation_cache = revocation_cache or get_default_revocation_cache()
    token_hash = hash_refresh_token(raw_refresh_token)

    if revocation_cache.is_revoked(token_hash):
        raise InvalidRefreshTokenError

    session_factory = get_session_factory()
    with session_factory() as session:
        record = get_refresh_token_by_hash(session, token_hash)
        now = datetime.now(timezone.utc)
        if (
            record is None
            or record.revoked_at is not None
            or _as_aware_utc(record.expires_at) < now
        ):
            raise InvalidRefreshTokenError

        user = session.get(UserRecord, record.user_id)
        if user is None:
            raise InvalidRefreshTokenError
        if not user.is_active:
            raise AccountDisabledError

        remaining_ttl = max(0, int((_as_aware_utc(record.expires_at) - now).total_seconds()))
        revoke_refresh_token(session, record)
        session.commit()

    revocation_cache.mark_revoked(token_hash, remaining_ttl)
    return _issue_tokens(user, settings)


def logout(raw_refresh_token: str, revocation_cache: RevocationCache | None = None) -> None:
    """Revoke `raw_refresh_token`. A no-op if it's already unknown or already revoked."""
    revocation_cache = revocation_cache or get_default_revocation_cache()
    token_hash = hash_refresh_token(raw_refresh_token)

    session_factory = get_session_factory()
    with session_factory() as session:
        record = get_refresh_token_by_hash(session, token_hash)
        if record is None or record.revoked_at is not None:
            return
        now = datetime.now(timezone.utc)
        remaining_ttl = max(0, int((_as_aware_utc(record.expires_at) - now).total_seconds()))
        revoke_refresh_token(session, record)
        session.commit()

    revocation_cache.mark_revoked(token_hash, remaining_ttl)


def list_all_users() -> list[UserRecord]:
    """Return every registered user, ordered by creation time."""
    session_factory = get_session_factory()
    with session_factory() as session:
        return list_users(session)


def get_user_profile(user_id: uuid.UUID) -> UserRecord:
    """Return `user_id`'s own profile. Raises `UserNotFoundError` if unknown.

    Backs `GET /auth/me` -- unlike `list_all_users`/admin endpoints, this is available to any
    authenticated caller for their own ID only (`app/auth/router.py` always passes
    `current_user.id`, never a caller-supplied one).
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        user = get_user_by_id(session, user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        return user


def set_user_active_status(user_id: uuid.UUID, is_active: bool) -> UserRecord:
    """Enable or disable `user_id`'s account. Raises `UserNotFoundError` if unknown."""
    session_factory = get_session_factory()
    with session_factory() as session:
        user = get_user_by_id(session, user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        set_user_active(session, user, is_active)
        session.commit()
        session.refresh(user)
        return user


def revoke_user_sessions(user_id: uuid.UUID) -> None:
    """Revoke every active refresh token belonging to `user_id`. Raises `UserNotFoundError` if unknown.

    A DB-only bulk revoke -- deliberately doesn't touch the revocation cache (which is a
    fast-path optimization, not authoritative; see `refresh_access_token`'s DB fallback check).
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        user = get_user_by_id(session, user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        revoke_all_refresh_tokens_for_user(session, user_id)
        session.commit()


def _delete_owner_faiss_index(owner_id: uuid.UUID) -> None:
    """Best-effort delete of `owner_id`'s on-disk FAISS index file (ERP-031), if one exists.

    Not load-bearing: the user and their DB rows are already gone by the time this runs, so a
    failure here (e.g. a permissions issue) leaves only a harmless orphaned file, not a
    correctness problem -- logged, not raised.
    """
    settings = get_embedding_settings()
    store = OwnerFaissIndexStore(settings.faiss_index_dir, settings.dimension)
    try:
        Path(store.path_for(owner_id)).unlink(missing_ok=True)
    except OSError:
        logger.exception("Failed to delete FAISS index file for owner %s", owner_id)


def delete_user(user_id: uuid.UUID, acting_admin_id: uuid.UUID) -> None:
    """Permanently delete `user_id` and every row they own. Irreversible -- no undo.

    Raises `CannotDeleteSelfError` if `user_id == acting_admin_id` (blocks an easy self-lockout
    footgun). Raises `UserNotFoundError` if `user_id` doesn't exist.
    """
    if user_id == acting_admin_id:
        raise CannotDeleteSelfError(user_id)

    session_factory = get_session_factory()
    with session_factory() as session:
        if get_user_by_id(session, user_id) is None:
            raise UserNotFoundError(user_id)
        delete_user_and_owned_data(session, user_id)
        session.commit()

    _delete_owner_faiss_index(user_id)


def _is_oidc_configured(settings: AuthSettings) -> bool:
    return bool(
        settings.oidc_issuer
        and settings.oidc_client_id
        and settings.oidc_client_secret
        and settings.oidc_redirect_uri
    )


def _check_oidc_provider(provider: str, settings: AuthSettings) -> None:
    if not _is_oidc_configured(settings) or provider != settings.oidc_provider_name:
        raise OidcNotConfiguredError(provider)


class OidcLoginStart(NamedTuple):
    """The result of starting an OIDC login: where to redirect, and the cookie to set alongside it."""

    authorization_url: str
    state_cookie_value: str
    state_cookie_max_age_seconds: int


def start_oidc_login(provider: str, settings: AuthSettings | None = None) -> OidcLoginStart:
    """Build the authorization URL to redirect the caller's browser to, starting an OIDC login.

    The returned `state_cookie_value` must be set by the router as an `HttpOnly`/`Secure`/
    `SameSite=Lax` cookie alongside the redirect -- it carries the PKCE verifier and nonce out of
    any URL entirely, and is what `complete_oidc_login` uses to both recover them and defend
    against login CSRF (see `app/auth/oidc.py`'s `create_oidc_cookie`/`decode_oidc_cookie`).

    Raises `OidcNotConfiguredError` if `provider` doesn't match the configured provider name, or
    OIDC isn't configured at all.
    """
    settings = settings or get_auth_settings()
    _check_oidc_provider(provider, settings)

    metadata = oidc.discover_metadata(cast(str, settings.oidc_issuer), settings)
    code_verifier, code_challenge = oidc.generate_pkce_pair()
    nonce = secrets.token_urlsafe(16)
    state = oidc.generate_state_value()
    cookie_value = oidc.create_oidc_cookie(state, code_verifier, nonce, settings)
    authorization_url = oidc.build_authorization_url(metadata, settings, state, nonce, code_challenge)
    return OidcLoginStart(authorization_url, cookie_value, settings.oidc_state_expire_seconds)


def _resolve_oidc_user(provider: str, external_id: str, email: str, email_verified: bool) -> UserRecord:
    """Look up, link, or create the local user for an OIDC identity. Commits its own transaction.

    See the design spec's Account Linking Decision: an existing account with a matching email is
    only auto-linked when `email_verified` is `True`; otherwise raises `OidcAccountConflictError`
    without creating anything.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        identity = get_oidc_identity(session, provider, external_id)
        if identity is not None:
            user = session.get(UserRecord, identity.user_id)
            if user is None:  # pragma: no cover - unreachable: FK guarantees the user still exists
                raise InvalidCredentialsError
        else:
            existing = get_user_by_email(session, email)
            if existing is not None:
                if not email_verified:
                    raise OidcAccountConflictError(email)
                user = existing
            else:
                user = create_oidc_user(session, email)
            create_oidc_identity(session, user.id, provider, external_id, email)

        if not user.is_active:
            raise AccountDisabledError

        session.commit()
        return user


def complete_oidc_login(
    provider: str,
    code: str,
    state: str,
    state_cookie_value: str | None,
    settings: AuthSettings | None = None,
) -> TokenResponse:
    """Exchange an authorization code for tokens, resolve/link the user, and issue our own tokens.

    `state_cookie_value` is the `oidc_state` cookie the router read from the incoming request --
    required (not optional in practice) to recover the PKCE verifier/nonce and to bind this
    callback to the browser that started the flow (login-CSRF defense); a missing cookie raises
    `oidc.InvalidOidcStateError` exactly like a bad one, so a forged callback link with no cookie
    at all fails the same way as one with a mismatched cookie.

    Raises `OidcNotConfiguredError`, `oidc.InvalidOidcStateError`, `oidc.OidcTokenExchangeError`,
    `oidc.OidcTokenValidationError`, `OidcAccountConflictError`, or `AccountDisabledError` — see
    `app/auth/router.py` for the HTTP status each maps to.
    """
    settings = settings or get_auth_settings()
    _check_oidc_provider(provider, settings)

    if state_cookie_value is None:
        raise oidc.InvalidOidcStateError("missing OIDC state cookie")
    code_verifier, nonce = oidc.decode_oidc_cookie(state_cookie_value, state, settings)
    metadata = oidc.discover_metadata(cast(str, settings.oidc_issuer), settings)
    token_response = oidc.exchange_code_for_tokens(metadata, settings, code, code_verifier)
    try:
        id_token = token_response["id_token"]
    except KeyError as exc:
        raise oidc.OidcTokenExchangeError("IdP response did not include an id_token") from exc

    claims = oidc.verify_id_token(id_token, metadata, settings, nonce)
    try:
        external_id = cast(str, claims["sub"])
        email = cast(str, claims["email"])
    except KeyError as exc:
        raise oidc.OidcTokenValidationError("ID token missing required sub/email claim") from exc
    email_verified = bool(claims.get("email_verified", False))

    user = _resolve_oidc_user(provider, external_id, email, email_verified)
    return _issue_tokens(user, settings)
