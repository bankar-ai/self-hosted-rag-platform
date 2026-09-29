import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.auth.dependencies import get_current_user, require_role
from app.auth.security import create_access_token


def _fake_request(cookie_header: str, method: str = "GET") -> Request:
    """A minimal Starlette `Request` carrying just a `Cookie` header (ERP-116).

    `get_current_user` now reads the access token from a cookie, not a bearer-token string
    argument -- these unit tests build the smallest real `Request` that exercises that, rather
    than going through a full TestClient round-trip for a dependency-level check.
    """
    return Request({"type": "http", "method": method, "headers": [(b"cookie", cookie_header.encode())]})


def test_get_current_user_accepts_valid_token(auth_settings, monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    token = create_access_token(uuid.uuid4(), "user", auth_settings)

    current_user = get_current_user(_fake_request(f"access_token={token}"))

    assert current_user.role == "user"


def test_get_current_user_rejects_invalid_token(auth_settings, monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(_fake_request("access_token=garbage"))
    assert exc_info.value.status_code == 401


def test_get_current_user_rejects_mutating_request_without_matching_csrf_token(
    auth_settings, monkeypatch
):
    """ERP-116: a valid access token alone isn't enough for a state-changing request."""
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    token = create_access_token(uuid.uuid4(), "user", auth_settings)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(_fake_request(f"access_token={token}", method="POST"))
    assert exc_info.value.status_code == 403


def test_get_current_user_accepts_mutating_request_with_matching_csrf_token(auth_settings, monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    token = create_access_token(uuid.uuid4(), "user", auth_settings)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"cookie", f"access_token={token}; csrf_token=matching-value".encode()),
                (b"x-csrf-token", b"matching-value"),
            ],
        }
    )

    current_user = get_current_user(request)

    assert current_user.role == "user"


def test_get_current_user_rejects_missing_cookie(auth_settings, monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(_fake_request(""))
    assert exc_info.value.status_code == 401


def test_require_role_allows_matching_role(auth_settings, monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    token = create_access_token(uuid.uuid4(), "admin", auth_settings)
    current_user = get_current_user(_fake_request(f"access_token={token}"))

    checker = require_role("admin")
    assert checker(current_user).role == "admin"


def test_require_role_rejects_wrong_role(auth_settings, monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    token = create_access_token(uuid.uuid4(), "user", auth_settings)
    current_user = get_current_user(_fake_request(f"access_token={token}"))

    checker = require_role("admin")
    with pytest.raises(HTTPException) as exc_info:
        checker(current_user)
    assert exc_info.value.status_code == 403
