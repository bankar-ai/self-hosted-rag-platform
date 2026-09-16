from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_allowed_origin_gets_cors_header():
    """The default `CORS_ALLOWED_ORIGINS` (unset in the test environment) is `http://localhost:5173`.

    Origin allowlisting is baked into `CORSMiddleware` at app-construction time (import time),
    matching every other `*Settings` class in this app (e.g. `DATABASE_URL`) -- read once at
    process start, not re-evaluated per request. So this test exercises the real default rather
    than trying to monkeypatch an env var after the app (and its middleware) already exist.
    """
    response = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_disallowed_origin_gets_no_cors_header():
    response = client.options(
        "/auth/login",
        headers={
            "Origin": "https://not-allowed.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert "access-control-allow-origin" not in response.headers
