import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.auth import oidc
from app.auth.config import get_auth_settings
from app.core.db import get_session_factory
from app.generation.repository import append_message, get_or_create_conversation, set_message_feedback
from app.ingestion.repository import save_document_and_chunks
from app.ingestion.schemas import Chunk
from app.main import app

# base_url="https://..." (not the default http://testserver) so the OIDC flow's `Secure` cookie
# (app/auth/router.py's oidc_state cookie) actually round-trips through this client -- httpx's
# cookie jar won't send a Secure-flagged cookie back over a plain http:// request.
client = TestClient(app, base_url="https://testserver")

_OIDC_ENV = {
    "AUTH_OIDC_PROVIDER_NAME": "google",
    "AUTH_OIDC_ISSUER": "https://idp.example.com",
    "AUTH_OIDC_CLIENT_ID": "test-client-id",
    "AUTH_OIDC_CLIENT_SECRET": "test-client-secret",
    "AUTH_OIDC_REDIRECT_URI": "https://app.example.com/auth/oidc/google/callback",
}


@pytest.fixture(autouse=True)
def _isolate_oidc_cookie_jar():
    """Clear the shared `client`'s cookie jar before/after each test.

    `client` is module-level (shared across every test in this file, matching the rest of the
    suite's convention), so without this, an `oidc_state` cookie set by one test's `/login` call
    could leak into a later test that never called `/login` itself, making cookie-dependent
    assertions order-dependent.
    """
    client.cookies.clear()
    yield
    client.cookies.clear()


@pytest.fixture
def configured_oidc(monkeypatch):
    """Configure OIDC via env vars for the duration of one test, then restore."""
    for key, value in _OIDC_ENV.items():
        monkeypatch.setenv(key, value)
    get_auth_settings.cache_clear()
    try:
        yield
    finally:
        get_auth_settings.cache_clear()


def _canned_claims(**overrides) -> dict:
    claims = {"sub": "router-external-id", "email": "router-oidc@example.com", "email_verified": True}
    claims.update(overrides)
    return claims


def _stub_oidc_network(monkeypatch, claims: dict) -> None:
    monkeypatch.setattr(
        oidc,
        "discover_metadata",
        lambda issuer, settings: {
            "issuer": issuer,
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/jwks",
        },
    )
    monkeypatch.setattr(
        oidc, "exchange_code_for_tokens", lambda metadata, settings, code, verifier: {"id_token": "fake"}
    )
    monkeypatch.setattr(oidc, "verify_id_token", lambda id_token, metadata, settings, nonce: claims)


def test_register_then_login_then_refresh_then_logout():
    register_response = client.post(
        "/auth/register",
        json={"email": "router-test@example.com", "password": "a-long-enough-password"},
    )
    assert register_response.status_code == 201
    assert register_response.json()["role"] == "user"

    login_response = client.post(
        "/auth/login",
        json={"email": "router-test@example.com", "password": "a-long-enough-password"},
    )
    assert login_response.status_code == 200
    tokens = login_response.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]

    refresh_response = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh_response.status_code == 200
    new_tokens = refresh_response.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    logout_response = client.post("/auth/logout", json={"refresh_token": new_tokens["refresh_token"]})
    assert logout_response.status_code == 204

    reuse_response = client.post(
        "/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert reuse_response.status_code == 401


def test_me_returns_the_authenticated_caller_profile():
    client.post(
        "/auth/register",
        json={"email": "me-router-test@example.com", "password": "a-long-enough-password"},
    )
    login_response = client.post(
        "/auth/login",
        json={"email": "me-router-test@example.com", "password": "a-long-enough-password"},
    )
    access_token = login_response.json()["access_token"]

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "me-router-test@example.com"
    assert body["role"] == "user"
    assert body["is_active"] is True


def test_me_rejects_missing_token():
    response = client.get("/auth/me")

    assert response.status_code == 401


def test_delete_me_rejects_missing_token():
    response = client.delete("/auth/me")

    assert response.status_code == 401


def test_delete_me_removes_caller_and_their_owned_data_and_requires_no_user_id_param():
    email = f"delete-me-router-{uuid.uuid4()}@example.com"
    password = "a-long-enough-password"
    client.post("/auth/register", json={"email": email, "password": password})
    login_response = client.post("/auth/login", json={"email": email, "password": password})
    tokens = login_response.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    user_id = uuid.UUID(client.get("/auth/me", headers=headers).json()["id"])

    # Give this user owned data across every table `delete_user_and_owned_data` must clean up --
    # mirrors test_admin.py's test_admin_can_delete_a_user_and_their_owned_data, just deleting via
    # the self-service endpoint (no user_id in the URL; it always acts on the caller) instead of
    # the admin one.
    session_factory = get_session_factory()
    with session_factory() as session:
        chunk = Chunk(
            chunk_id=f"chunk-{uuid.uuid4()}",
            document_id=f"doc-{uuid.uuid4()}",
            chunk_index=0,
            text="some owned content",
            section_path=["Intro"],
            page_start=1,
            page_end=1,
            char_count=len("some owned content"),
            parser_used="fast",
            source_filename="doc.pdf",
        )
        save_document_and_chunks(session, chunk.document_id, chunk.source_filename, [chunk], user_id)
        conversation_id = uuid.uuid4()
        get_or_create_conversation(session, conversation_id, user_id)
        append_message(session, conversation_id, "user", "hello")
        assistant_message = append_message(session, conversation_id, "assistant", "hi there")
        set_message_feedback(session, assistant_message.id, user_id, "up")
        session.commit()

    response = client.delete("/auth/me", headers=headers)
    assert response.status_code == 204

    # Refresh tokens are cascade-deleted with the user, so the old refresh token must now fail --
    # even though the already-issued access token (stateless, not checked against the DB per
    # request) would still decode fine within its own unexpired lifetime.
    refresh_response = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh_response.status_code == 401


def test_register_rejects_duplicate_email():
    client.post(
        "/auth/register",
        json={"email": "dup-router-test@example.com", "password": "a-long-enough-password"},
    )
    response = client.post(
        "/auth/register",
        json={"email": "dup-router-test@example.com", "password": "another-password"},
    )
    assert response.status_code == 409


def test_login_rejects_wrong_password():
    client.post(
        "/auth/register",
        json={"email": "wrongpw-router-test@example.com", "password": "a-long-enough-password"},
    )
    response = client.post(
        "/auth/login",
        json={"email": "wrongpw-router-test@example.com", "password": "not-the-password"},
    )
    assert response.status_code == 401


def test_refresh_rejects_unknown_token():
    response = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert response.status_code == 401


def test_oidc_login_redirects_to_authorization_endpoint(configured_oidc, monkeypatch):
    _stub_oidc_network(monkeypatch, _canned_claims())

    response = client.get("/auth/oidc/google/login", follow_redirects=False)

    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("https://idp.example.com/authorize?")
    params = parse_qs(urlparse(location).query)
    assert params["client_id"] == ["test-client-id"]
    assert params["redirect_uri"] == ["https://app.example.com/auth/oidc/google/callback"]
    assert params["code_challenge_method"] == ["S256"]
    assert "state" in params
    assert "nonce" in params


def test_oidc_login_404_for_unknown_provider(configured_oidc, monkeypatch):
    _stub_oidc_network(monkeypatch, _canned_claims())
    response = client.get("/auth/oidc/okta/login", follow_redirects=False)
    assert response.status_code == 404


def test_oidc_login_404_when_not_configured():
    response = client.get("/auth/oidc/google/login", follow_redirects=False)
    assert response.status_code == 404


def test_oidc_callback_full_round_trip_creates_user_and_issues_tokens(configured_oidc, monkeypatch):
    claims = _canned_claims(sub="full-roundtrip-external-id", email="full-roundtrip@example.com")
    _stub_oidc_network(monkeypatch, claims)

    login_response = client.get("/auth/oidc/google/login", follow_redirects=False)
    state = parse_qs(urlparse(login_response.headers["location"]).query)["state"][0]

    callback_response = client.get(
        "/auth/oidc/google/callback", params={"code": "auth-code", "state": state}
    )

    assert callback_response.status_code == 200
    tokens = callback_response.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]

    # The issued access token works against a real protected endpoint, exactly like local login.
    me_response = client.get(
        "/admin/users", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me_response.status_code == 403  # not an admin -- but 403, not 401, proves the token is valid


def test_oidc_callback_rejects_missing_state_cookie(configured_oidc, monkeypatch):
    # No prior /login call in this test, so no oidc_state cookie was ever set.
    _stub_oidc_network(monkeypatch, _canned_claims())
    response = client.get(
        "/auth/oidc/google/callback", params={"code": "auth-code", "state": "not-a-real-state"}
    )
    assert response.status_code == 400


def test_oidc_callback_rejects_state_not_matching_cookie(configured_oidc, monkeypatch):
    # Simulates an attacker tampering with the `state` query parameter -- the cookie set by our
    # own /login call is present and valid, but doesn't match the (attacker-supplied) state.
    _stub_oidc_network(monkeypatch, _canned_claims())
    client.get("/auth/oidc/google/login", follow_redirects=False)

    response = client.get(
        "/auth/oidc/google/callback", params={"code": "auth-code", "state": "attacker-supplied-state"}
    )
    assert response.status_code == 400


def test_oidc_callback_404_for_unknown_provider(configured_oidc, monkeypatch):
    _stub_oidc_network(monkeypatch, _canned_claims())
    response = client.get(
        "/auth/oidc/okta/callback", params={"code": "auth-code", "state": "irrelevant"}
    )
    assert response.status_code == 404


def test_oidc_callback_conflict_when_email_exists_unverified(configured_oidc, monkeypatch):
    email = "router-conflict@example.com"
    client.post("/auth/register", json={"email": email, "password": "a-long-enough-password"})
    claims = _canned_claims(sub="router-conflict-external-id", email=email, email_verified=False)
    _stub_oidc_network(monkeypatch, claims)

    login_response = client.get("/auth/oidc/google/login", follow_redirects=False)
    state = parse_qs(urlparse(login_response.headers["location"]).query)["state"][0]

    response = client.get("/auth/oidc/google/callback", params={"code": "auth-code", "state": state})
    assert response.status_code == 409


def test_oidc_callback_502_when_token_exchange_fails(configured_oidc, monkeypatch):
    monkeypatch.setattr(
        oidc,
        "discover_metadata",
        lambda issuer, settings: {
            "issuer": issuer,
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/jwks",
        },
    )

    def _raise_exchange_error(metadata, settings, code, verifier):
        raise oidc.OidcTokenExchangeError

    monkeypatch.setattr(oidc, "exchange_code_for_tokens", _raise_exchange_error)

    login_response = client.get("/auth/oidc/google/login", follow_redirects=False)
    state = parse_qs(urlparse(login_response.headers["location"]).query)["state"][0]

    response = client.get("/auth/oidc/google/callback", params={"code": "auth-code", "state": state})
    assert response.status_code == 502


# Note on Finding 2 (final whole-branch review, uncaught InvalidHashError against the seeded
# `system` user): the literal seeded email "system@internal" has no dot in its domain part,
# so LoginRequest's own email-shape validator (app/auth/schemas.py's _EMAIL_PATTERN) rejects
# it with a 422 before the request body ever reaches app.auth.service.login -- the router
# can't be used to reach the buggy code path at all for this exact email. The actual fix and
# its regression coverage live one layer down: tests/auth/test_security.py asserts
# verify_password("anything", "!") returns False rather than raising, and
# tests/auth/test_service.py::test_login_against_seeded_system_user_raises_invalid_credentials
# asserts service.login (called directly, bypassing the router's email-shape gate) raises
# InvalidCredentialsError rather than an uncaught InvalidHashError.
