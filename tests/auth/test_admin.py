"""Router-level tests for the admin user-management endpoints."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_session_factory
from app.generation.repository import append_message, get_or_create_conversation, set_message_feedback
from app.ingestion.repository import save_document_and_chunks
from app.ingestion.schemas import Chunk
from app.main import app
from tests.auth_helpers import create_admin_and_get_headers, register_and_login

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_cookie_jar():
    """Clear the shared `client`'s cookie jar before/after each test (ERP-116).

    Cookie-based auth is client-scoped, not request-scoped like the old Authorization header
    was -- without this, a session left on `client` by one test would silently authenticate a
    later test that never logged in itself, making "requires authentication" assertions
    order-dependent. Mirrors the same guard `tests/auth/test_router.py` already has for its own
    OIDC state cookie.
    """
    client.cookies.clear()
    yield
    client.cookies.clear()


def test_non_admin_cannot_list_users():
    headers = register_and_login(client, "admin-forbidden")
    response = client.get("/admin/users", headers=headers)
    assert response.status_code == 403


def test_admin_can_list_users():
    admin_headers = create_admin_and_get_headers(client, "admin-list")
    # ERP-116: cookie-based auth is client-scoped -- registering the target user on the shared
    # `client` would overwrite the admin's session cookies with the target's.
    register_and_login(TestClient(app), "admin-list-target")

    response = client.get("/admin/users", headers=admin_headers)

    assert response.status_code == 200
    users = response.json()
    assert any(u["email"].startswith("admin-list-target-") for u in users)
    assert all("is_active" in u for u in users)


def test_admin_can_disable_and_reenable_a_user():
    admin_headers = create_admin_and_get_headers(client, "admin-disable")
    email = f"admin-disable-target-{uuid.uuid4()}@example.com"
    password = "a-long-enough-password"
    # ERP-116: a target login would overwrite the shared `client`'s cookies (admin's session)
    # with the target's -- use a dedicated client for every target-identity action.
    target_client = TestClient(app)
    target_client.post("/auth/register", json={"email": email, "password": password})
    target_client.post("/auth/login", json={"email": email, "password": password})
    users = client.get("/admin/users", headers=admin_headers).json()
    user_id = next(u["id"] for u in users if u["email"] == email)

    disable_response = client.patch(
        f"/admin/users/{user_id}", json={"is_active": False}, headers=admin_headers
    )
    assert disable_response.status_code == 200
    assert disable_response.json()["is_active"] is False

    disabled_login = target_client.post("/auth/login", json={"email": email, "password": password})
    assert disabled_login.status_code == 403

    enable_response = client.patch(
        f"/admin/users/{user_id}", json={"is_active": True}, headers=admin_headers
    )
    assert enable_response.status_code == 200
    assert enable_response.json()["is_active"] is True

    reenabled_login = target_client.post("/auth/login", json={"email": email, "password": password})
    assert reenabled_login.status_code == 200


def test_disable_unknown_user_returns_404():
    admin_headers = create_admin_and_get_headers(client, "admin-disable-404")
    response = client.patch(
        f"/admin/users/{uuid.uuid4()}", json={"is_active": False}, headers=admin_headers
    )
    assert response.status_code == 404


def test_admin_can_revoke_a_users_sessions():
    admin_headers = create_admin_and_get_headers(client, "admin-revoke")
    email = f"admin-revoke-target-{uuid.uuid4()}@example.com"
    password = "a-long-enough-password"
    # ERP-116: a dedicated client for the target both avoids clobbering the admin's session on
    # the shared `client` and naturally carries the target's refresh_token cookie afterward, so
    # the later /auth/refresh attempt (also cookie-based now, no request body) needs no manual
    # token extraction -- the JSON response no longer even carries the raw token value.
    target_client = TestClient(app)
    target_client.post("/auth/register", json={"email": email, "password": password})
    target_client.post("/auth/login", json={"email": email, "password": password})

    users = client.get("/admin/users", headers=admin_headers).json()
    user_id = next(u["id"] for u in users if u["email"] == email)

    revoke_response = client.post(f"/admin/users/{user_id}/revoke-sessions", headers=admin_headers)
    assert revoke_response.status_code == 204

    target_csrf_headers = {"X-CSRF-Token": target_client.cookies["csrf_token"]}
    refresh_response = target_client.post("/auth/refresh", headers=target_csrf_headers)
    assert refresh_response.status_code == 401


def test_revoke_sessions_for_unknown_user_returns_404():
    admin_headers = create_admin_and_get_headers(client, "admin-revoke-404")
    response = client.post(f"/admin/users/{uuid.uuid4()}/revoke-sessions", headers=admin_headers)
    assert response.status_code == 404


def test_admin_endpoints_require_authentication_at_all():
    assert client.get("/admin/users").status_code == 401
    assert client.patch(f"/admin/users/{uuid.uuid4()}", json={"is_active": False}).status_code == 401
    assert client.post(f"/admin/users/{uuid.uuid4()}/revoke-sessions").status_code == 401
    assert client.delete(f"/admin/users/{uuid.uuid4()}").status_code == 401


def test_non_admin_cannot_delete_a_user():
    headers = register_and_login(client, "admin-delete-forbidden")
    response = client.delete(f"/admin/users/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 403


def test_delete_unknown_user_returns_404():
    admin_headers = create_admin_and_get_headers(client, "admin-delete-404")
    response = client.delete(f"/admin/users/{uuid.uuid4()}", headers=admin_headers)
    assert response.status_code == 404


def test_admin_cannot_delete_their_own_account():
    admin_headers = create_admin_and_get_headers(client, "admin-delete-self")
    users = client.get("/admin/users", headers=admin_headers).json()
    self_id = next(
        u["id"] for u in users if u["email"].startswith("admin-delete-self-")
    )

    response = client.delete(f"/admin/users/{self_id}", headers=admin_headers)

    assert response.status_code == 409


def test_admin_can_delete_a_user_and_their_owned_data():
    admin_headers = create_admin_and_get_headers(client, "admin-delete")
    email = f"admin-delete-target-{uuid.uuid4()}@example.com"
    password = "a-long-enough-password"
    client.post("/auth/register", json={"email": email, "password": password})
    users = client.get("/admin/users", headers=admin_headers).json()
    user_id = uuid.UUID(next(u["id"] for u in users if u["email"] == email))

    # Give this user owned data across every table `delete_user_and_owned_data` must clean up.
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
        # A rated message (ERP-045) -- regression coverage for a real bug found live: deleting
        # conversation_messages before message_feedback violates a foreign key.
        set_message_feedback(session, assistant_message.id, user_id, "up")
        session.commit()

    response = client.delete(f"/admin/users/{user_id}", headers=admin_headers)
    assert response.status_code == 204

    remaining_users = client.get("/admin/users", headers=admin_headers).json()
    assert all(u["id"] != str(user_id) for u in remaining_users)

    login_response = client.post("/auth/login", json={"email": email, "password": password})
    assert login_response.status_code == 401
