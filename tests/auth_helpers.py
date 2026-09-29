"""Shared test helpers for authenticating against a live TestClient.

ERP-116: auth moved from Authorization headers (backed by localStorage) to httpOnly cookies.
`TestClient` has its own cookie jar (mirrors `requests.Session`), so once `/auth/login` sets a
cookie on a given `client`, every subsequent request through that *same* `client` instance sends
it automatically -- no manual header needed for the access/refresh tokens themselves. Callers
still need the returned headers dict for the CSRF double-submit token on any mutating request.
"""

import secrets
import uuid

from fastapi.testclient import TestClient

from app.auth.config import get_auth_settings
from app.auth.cookies import CSRF_TOKEN_COOKIE, CSRF_TOKEN_HEADER
from app.auth.repository import create_user
from app.auth.security import create_access_token, generate_refresh_token, hash_password
from app.core.db import get_session_factory


def register_and_login(client: TestClient, prefix: str) -> dict[str, str]:
    """Register a fresh throwaway user (`prefix` + a UUID, so tests never collide) and log in.

    Cookies land on `client` automatically (its own cookie jar). Returns the `X-CSRF-Token`
    header a mutating request must also pass -- `headers=register_and_login(client, "x")` reads
    the same at every call site as it did when this returned an Authorization header.
    """
    email = f"{prefix}-{uuid.uuid4()}@example.com"
    password = "a-long-enough-password"
    client.post("/auth/register", json={"email": email, "password": password})
    client.post("/auth/login", json={"email": email, "password": password})
    csrf_token = client.cookies.get(CSRF_TOKEN_COOKIE)
    assert csrf_token is not None
    return {CSRF_TOKEN_HEADER: csrf_token}


def create_admin_and_get_headers(client: TestClient, prefix: str) -> dict[str, str]:
    """Create a fresh throwaway `admin`-role user and set its cookies directly on `client`.

    Created via the repository (no self-service admin registration exists), so unlike
    `register_and_login` this can't go through `/auth/login` to get cookies set naturally --
    sets them on `client`'s cookie jar directly instead of issuing a real HTTP response.
    """
    email = f"{prefix}-{uuid.uuid4()}@example.com"
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, email, hash_password("a-long-enough-password"), role="admin")
        session.commit()
        user_id, role = user.id, user.role
    settings = get_auth_settings()
    access_token = create_access_token(user_id, role, settings)
    refresh_token = generate_refresh_token()
    csrf_token = secrets.token_urlsafe(32)
    client.cookies.set("access_token", access_token)
    client.cookies.set("refresh_token", refresh_token)
    client.cookies.set(CSRF_TOKEN_COOKIE, csrf_token)
    return {CSRF_TOKEN_HEADER: csrf_token}
