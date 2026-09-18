"""Local smoke test for the Cloud Run docling service. Run manually, not part of main-project CI."""

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_health_returns_ok():
    """The liveness endpoint responds without needing docling to actually run."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
