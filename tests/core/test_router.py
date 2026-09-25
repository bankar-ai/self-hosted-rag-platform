from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_200_when_database_is_reachable():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_returns_503_when_database_is_unreachable(monkeypatch):
    @contextmanager
    def _broken_session():
        class _Session:
            def execute(self, *args, **kwargs):
                raise RuntimeError("connection refused")

        yield _Session()

    monkeypatch.setattr("app.core.router.get_session_factory", lambda: _broken_session)

    response = client.get("/health")

    assert response.status_code == 503
