import uuid

import pytest

from app.auth import oidc
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
    login,
    logout,
    refresh_access_token,
    register_user,
    revoke_user_sessions,
    set_user_active_status,
    start_oidc_login,
)

# Mirrors the migration-seeded `system` user (alembic/versions/d456a2953c15_...): a fixed
# account with hashed_password="!" (deliberately not a valid Argon2 hash, so it can never
# authenticate). The test schema is built via `Base.metadata.create_all` (tests/conftest.py),
# which does not run the migration's data seed, so this row is inserted directly here.
_SYSTEM_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
_SYSTEM_USER_EMAIL = "system@internal"
_SYSTEM_USER_HASH = "!"


def _ensure_system_user_seeded():
    from app.auth.models import UserRecord
    from app.core.db import get_session_factory

    session_factory = get_session_factory()
    with session_factory() as session:
        if session.get(UserRecord, _SYSTEM_USER_ID) is None:
            session.add(
                UserRecord(
                    id=_SYSTEM_USER_ID, email=_SYSTEM_USER_EMAIL, hashed_password=_SYSTEM_USER_HASH
                )
            )
            session.commit()


def test_register_user_then_login_succeeds(auth_settings):
    register_user("service-test@example.com", "a-long-enough-password")

    tokens = login("service-test@example.com", "a-long-enough-password", settings=auth_settings)

    assert tokens.access_token
    assert tokens.refresh_token


def test_register_user_rejects_duplicate_email():
    register_user("dup-test@example.com", "a-long-enough-password")
    with pytest.raises(EmailAlreadyRegisteredError):
        register_user("dup-test@example.com", "another-password")


def test_login_rejects_wrong_password(auth_settings):
    register_user("wrongpw-test@example.com", "a-long-enough-password")
    with pytest.raises(InvalidCredentialsError):
        login("wrongpw-test@example.com", "not-the-password", settings=auth_settings)


def test_login_rejects_unknown_email(auth_settings):
    with pytest.raises(InvalidCredentialsError):
        login("nobody-test@example.com", "whatever", settings=auth_settings)


def test_refresh_rotates_token_and_old_token_becomes_invalid(auth_settings):
    register_user("refresh-flow@example.com", "a-long-enough-password")
    tokens = login("refresh-flow@example.com", "a-long-enough-password", settings=auth_settings)

    new_tokens = refresh_access_token(tokens.refresh_token, settings=auth_settings)
    assert new_tokens.refresh_token != tokens.refresh_token

    with pytest.raises(InvalidRefreshTokenError):
        refresh_access_token(tokens.refresh_token, settings=auth_settings)


def test_refresh_rejects_unknown_token(auth_settings):
    with pytest.raises(InvalidRefreshTokenError):
        refresh_access_token("not-a-real-refresh-token", settings=auth_settings)


def test_logout_revokes_token(auth_settings):
    register_user("logout-test@example.com", "a-long-enough-password")
    tokens = login("logout-test@example.com", "a-long-enough-password", settings=auth_settings)

    logout(tokens.refresh_token)

    with pytest.raises(InvalidRefreshTokenError):
        refresh_access_token(tokens.refresh_token, settings=auth_settings)


def test_logout_of_unknown_token_is_a_no_op():
    logout("not-a-real-refresh-token")


def test_list_all_users_includes_registered_user():
    register_user("list-test@example.com", "a-long-enough-password")
    emails = [user.email for user in list_all_users()]
    assert "list-test@example.com" in emails


def test_set_user_active_status_toggles_flag():
    user = register_user("disable-test@example.com", "a-long-enough-password")
    assert user.is_active is True

    disabled = set_user_active_status(user.id, False)
    assert disabled.is_active is False

    enabled = set_user_active_status(user.id, True)
    assert enabled.is_active is True


def test_set_user_active_status_raises_for_unknown_user():
    with pytest.raises(UserNotFoundError):
        set_user_active_status(uuid.uuid4(), False)


def test_disabled_user_cannot_login(auth_settings):
    user = register_user("login-disabled@example.com", "a-long-enough-password")
    set_user_active_status(user.id, False)

    with pytest.raises(AccountDisabledError):
        login("login-disabled@example.com", "a-long-enough-password", settings=auth_settings)


def test_disabling_user_after_login_blocks_refresh_but_not_the_existing_access_token(
    auth_settings,
):
    # Documents the accepted trade-off from the design: disabling a user blocks new logins and
    # refreshes immediately, but an already-issued access token is stateless and keeps working
    # until its own natural (short) expiry -- there is no per-request DB check.
    user = register_user("disable-after-login@example.com", "a-long-enough-password")
    tokens = login(
        "disable-after-login@example.com", "a-long-enough-password", settings=auth_settings
    )

    set_user_active_status(user.id, False)

    from app.auth.security import decode_access_token

    current_user = decode_access_token(tokens.access_token, auth_settings)
    assert current_user.id == user.id

    with pytest.raises(AccountDisabledError):
        refresh_access_token(tokens.refresh_token, settings=auth_settings)


def test_revoke_user_sessions_invalidates_refresh_token(auth_settings):
    user = register_user("revoke-sessions@example.com", "a-long-enough-password")
    tokens = login("revoke-sessions@example.com", "a-long-enough-password", settings=auth_settings)

    revoke_user_sessions(user.id)

    with pytest.raises(InvalidRefreshTokenError):
        refresh_access_token(tokens.refresh_token, settings=auth_settings)


def test_revoke_user_sessions_raises_for_unknown_user():
    with pytest.raises(UserNotFoundError):
        revoke_user_sessions(uuid.uuid4())


def test_delete_user_raises_for_unknown_user():
    with pytest.raises(UserNotFoundError):
        delete_user(uuid.uuid4(), acting_admin_id=uuid.uuid4())


def test_delete_user_raises_when_deleting_self():
    admin = register_user("delete-self@example.com", "a-long-enough-password")
    with pytest.raises(CannotDeleteSelfError):
        delete_user(admin.id, acting_admin_id=admin.id)


def test_delete_user_removes_the_user(auth_settings):
    user = register_user("delete-me@example.com", "a-long-enough-password")

    delete_user(user.id, acting_admin_id=uuid.uuid4())

    with pytest.raises(InvalidCredentialsError):
        login("delete-me@example.com", "a-long-enough-password", settings=auth_settings)


def test_delete_user_swallows_faiss_index_deletion_failure(monkeypatch):
    user = register_user("delete-faiss-failure@example.com", "a-long-enough-password")

    def _raise_os_error(self, missing_ok=False):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.unlink", _raise_os_error)

    delete_user(user.id, acting_admin_id=uuid.uuid4())  # must not raise


def _canned_claims(**overrides) -> dict:
    claims = {
        "sub": "external-id-1",
        "email": "oidc-service-test@example.com",
        "email_verified": True,
    }
    claims.update(overrides)
    return claims


def _patch_oidc_flow(monkeypatch, claims: dict) -> None:
    """Stub out every network/crypto call `complete_oidc_login` makes, returning `claims`."""
    monkeypatch.setattr(
        oidc, "decode_oidc_cookie", lambda cookie_value, state, settings: ("verifier", "nonce")
    )
    monkeypatch.setattr(oidc, "discover_metadata", lambda issuer, settings: {"token_endpoint": "x"})
    monkeypatch.setattr(
        oidc, "exchange_code_for_tokens", lambda metadata, settings, code, verifier: {"id_token": "fake"}
    )
    monkeypatch.setattr(oidc, "verify_id_token", lambda id_token, metadata, settings, nonce: claims)


def test_start_oidc_login_raises_when_not_configured(auth_settings):
    with pytest.raises(OidcNotConfiguredError):
        start_oidc_login("google", settings=auth_settings)


def test_start_oidc_login_raises_for_wrong_provider_name(oidc_settings):
    with pytest.raises(OidcNotConfiguredError):
        start_oidc_login("okta", settings=oidc_settings)


def test_start_oidc_login_returns_authorization_url(oidc_settings, monkeypatch):
    monkeypatch.setattr(
        oidc,
        "discover_metadata",
        lambda issuer, settings: {"authorization_endpoint": "https://idp.example.com/authorize"},
    )
    login_start = start_oidc_login("google", settings=oidc_settings)
    assert login_start.authorization_url.startswith("https://idp.example.com/authorize?")
    assert "code_challenge=" in login_start.authorization_url
    assert "state=" in login_start.authorization_url
    assert login_start.state_cookie_value
    assert login_start.state_cookie_max_age_seconds == oidc_settings.oidc_state_expire_seconds


def test_complete_oidc_login_creates_new_user_and_issues_tokens(oidc_settings, monkeypatch):
    claims = _canned_claims(email="new-oidc-user@example.com")
    _patch_oidc_flow(monkeypatch, claims)

    tokens = complete_oidc_login("google", "auth-code", "state", "cookie-value", settings=oidc_settings)

    assert tokens.access_token
    assert tokens.refresh_token

    from app.auth.repository import get_user_by_email
    from app.core.db import get_session_factory

    with get_session_factory()() as session:
        user = get_user_by_email(session, "new-oidc-user@example.com")
        assert user is not None
        assert user.hashed_password is None


def test_complete_oidc_login_reuses_existing_linked_identity(oidc_settings, monkeypatch):
    claims = _canned_claims(sub="repeat-external-id", email="repeat-oidc-user@example.com")
    _patch_oidc_flow(monkeypatch, claims)

    first_tokens = complete_oidc_login("google", "code-1", "state-1", "cookie-value", settings=oidc_settings)
    second_tokens = complete_oidc_login("google", "code-2", "state-2", "cookie-value", settings=oidc_settings)

    from app.auth.security import decode_access_token

    first_user = decode_access_token(first_tokens.access_token, oidc_settings)
    second_user = decode_access_token(second_tokens.access_token, oidc_settings)
    assert first_user.id == second_user.id


def test_complete_oidc_login_auto_links_verified_email_to_existing_local_user(oidc_settings, monkeypatch):
    local_user = register_user("linkable-oidc-user@example.com", "a-long-enough-password")
    claims = _canned_claims(sub="linkable-external-id", email="linkable-oidc-user@example.com")
    _patch_oidc_flow(monkeypatch, claims)

    tokens = complete_oidc_login("google", "auth-code", "state", "cookie-value", settings=oidc_settings)

    from app.auth.security import decode_access_token

    linked_user = decode_access_token(tokens.access_token, oidc_settings)
    assert linked_user.id == local_user.id

    # Local login still works unchanged after linking.
    local_login_tokens = login(
        "linkable-oidc-user@example.com", "a-long-enough-password", settings=oidc_settings
    )
    assert local_login_tokens.access_token


def test_complete_oidc_login_rejects_conflict_when_email_not_verified(oidc_settings, monkeypatch):
    register_user("unverified-conflict-user@example.com", "a-long-enough-password")
    claims = _canned_claims(
        sub="conflict-external-id", email="unverified-conflict-user@example.com", email_verified=False
    )
    _patch_oidc_flow(monkeypatch, claims)

    with pytest.raises(OidcAccountConflictError):
        complete_oidc_login("google", "auth-code", "state", "cookie-value", settings=oidc_settings)

    from app.auth.repository import get_oidc_identity
    from app.core.db import get_session_factory

    with get_session_factory()() as session:
        assert get_oidc_identity(session, "google", "conflict-external-id") is None


def test_complete_oidc_login_rejects_conflict_when_email_verified_claim_missing(oidc_settings, monkeypatch):
    register_user("missing-verified-claim-user@example.com", "a-long-enough-password")
    claims = {"sub": "missing-claim-external-id", "email": "missing-verified-claim-user@example.com"}
    _patch_oidc_flow(monkeypatch, claims)

    with pytest.raises(OidcAccountConflictError):
        complete_oidc_login("google", "auth-code", "state", "cookie-value", settings=oidc_settings)


def test_complete_oidc_login_raises_for_disabled_linked_account(oidc_settings, monkeypatch):
    claims = _canned_claims(sub="disabled-external-id", email="disabled-oidc-user@example.com")
    _patch_oidc_flow(monkeypatch, claims)
    complete_oidc_login("google", "code-1", "state-1", "cookie-value", settings=oidc_settings)

    from app.auth.repository import get_user_by_email
    from app.core.db import get_session_factory

    with get_session_factory()() as session:
        user = get_user_by_email(session, "disabled-oidc-user@example.com")
    set_user_active_status(user.id, False)

    with pytest.raises(AccountDisabledError):
        complete_oidc_login("google", "code-2", "state-2", "cookie-value", settings=oidc_settings)


def test_complete_oidc_login_raises_when_not_configured(auth_settings):
    with pytest.raises(OidcNotConfiguredError):
        complete_oidc_login("google", "code", "state", "cookie-value", settings=auth_settings)


def test_complete_oidc_login_raises_when_state_cookie_missing(oidc_settings):
    with pytest.raises(oidc.InvalidOidcStateError):
        complete_oidc_login("google", "code", "state", None, settings=oidc_settings)


def test_complete_oidc_login_propagates_invalid_state(oidc_settings, monkeypatch):
    def _raise_invalid_state(cookie_value, state, settings):
        raise oidc.InvalidOidcStateError

    monkeypatch.setattr(oidc, "decode_oidc_cookie", _raise_invalid_state)

    with pytest.raises(oidc.InvalidOidcStateError):
        complete_oidc_login("google", "code", "bad-state", "cookie-value", settings=oidc_settings)


def test_complete_oidc_login_raises_when_id_token_missing_from_response(oidc_settings, monkeypatch):
    monkeypatch.setattr(
        oidc, "decode_oidc_cookie", lambda cookie_value, state, settings: ("verifier", "nonce")
    )
    monkeypatch.setattr(oidc, "discover_metadata", lambda issuer, settings: {})
    monkeypatch.setattr(
        oidc, "exchange_code_for_tokens", lambda metadata, settings, code, verifier: {"access_token": "x"}
    )

    with pytest.raises(oidc.OidcTokenExchangeError):
        complete_oidc_login("google", "code", "state", "cookie-value", settings=oidc_settings)


def test_complete_oidc_login_raises_when_claims_missing_sub_or_email(oidc_settings, monkeypatch):
    _patch_oidc_flow(monkeypatch, {"email_verified": True})

    with pytest.raises(oidc.OidcTokenValidationError):
        complete_oidc_login("google", "code", "state", "cookie-value", settings=oidc_settings)


def test_login_against_seeded_system_user_raises_invalid_credentials(auth_settings):
    # Finding 2 (final whole-branch review): verify_password used to only catch
    # VerifyMismatchError, so authenticating against the seeded system user's "!"
    # (not-a-valid-Argon2-hash) placeholder raised an uncaught InvalidHashError instead of
    # the intended InvalidCredentialsError -- an uncaught exception (500 at the router) that
    # also doubled as a user-enumeration oracle (500 vs 401 confirms the account exists).
    _ensure_system_user_seeded()
    with pytest.raises(InvalidCredentialsError):
        login(_SYSTEM_USER_EMAIL, "anything-at-all", settings=auth_settings)
