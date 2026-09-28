import time

import pytest
from fastapi.testclient import TestClient

from app.core.rate_limit import RateLimiter
from app.embedding.client import OllamaEmbeddingClient
from app.embedding.config import get_embedding_settings
from app.main import app
from tests.auth_helpers import register_and_login

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_cookie_jar():
    """Clear the shared `client`'s cookie jar before/after each test (ERP-116).

    Cookie-based auth is client-scoped, not request-scoped like the old Authorization header
    was -- without this, a session left on `client` by one test could silently authenticate a
    later test that never logged in itself.
    """
    client.cookies.clear()
    yield
    client.cookies.clear()


def _register_and_login(prefix: str) -> dict[str, str]:
    return register_and_login(client, prefix)


@pytest.fixture
def auth_headers():
    return _register_and_login("retrieval-test")


@pytest.fixture(autouse=True)
def _stub_embedding_backend(monkeypatch, tmp_path):
    """Stub Ollama and redirect the FAISS index to a temp path.

    Same pattern as the ingestion router's tests — POST /retrieval/query uses production
    defaults with no injected fakes.
    """
    monkeypatch.setenv("EMBEDDING_FAISS_INDEX_DIR", str(tmp_path / "retrieval_router_index"))
    get_embedding_settings.cache_clear()

    def _fake_embed(self, texts):
        dimension = get_embedding_settings().dimension
        return [[0.1] * dimension for _ in texts]

    monkeypatch.setattr(OllamaEmbeddingClient, "embed", _fake_embed)
    try:
        yield
    finally:
        get_embedding_settings.cache_clear()


def test_query_on_empty_index_returns_empty_results(auth_headers):
    response = client.post("/retrieval/query", json={"query": "anything"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"results": []}


def test_query_rejects_empty_query_string(auth_headers):
    response = client.post("/retrieval/query", json={"query": ""}, headers=auth_headers)
    assert response.status_code == 422


def test_query_past_the_rate_limit_returns_429_with_retry_after(
    auth_headers, monkeypatch, rate_limit_settings
):
    limited_settings = rate_limit_settings.model_copy(update={"requests_per_minute": 1})
    monkeypatch.setattr(
        "app.auth.dependencies.get_default_rate_limiter", lambda: RateLimiter(limited_settings)
    )
    first = client.post("/retrieval/query", json={"query": "anything"}, headers=auth_headers)
    assert first.status_code == 200
    second = client.post("/retrieval/query", json={"query": "anything"}, headers=auth_headers)
    assert second.status_code == 429
    assert "Retry-After" in second.headers


def test_query_rejects_top_k_out_of_bounds(auth_headers):
    response = client.post("/retrieval/query", json={"query": "x", "top_k": 0}, headers=auth_headers)
    assert response.status_code == 422


def test_query_returns_503_when_embedding_backend_unavailable(monkeypatch, auth_headers):
    def _raise_embed(self, texts):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(OllamaEmbeddingClient, "embed", _raise_embed)

    response = client.post("/retrieval/query", json={"query": "anything"}, headers=auth_headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "Retrieval query failed"}


def test_query_returns_ingested_chunk(simple_text_pdf, auth_headers):
    with open(simple_text_pdf, "rb") as pdf_file:
        upload = client.post(
            "/ingestion/pdf",
            files={"file": ("simple.pdf", pdf_file, "application/pdf")},
            headers=auth_headers,
        )
    assert upload.status_code == 202
    job_id = upload.json()["job_id"]

    deadline = time.monotonic() + 60.0
    status_body = None
    while time.monotonic() < deadline:
        status_response = client.get(f"/ingestion/jobs/{job_id}", headers=auth_headers)
        status_body = status_response.json()
        if status_body["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert status_body is not None
    assert status_body["status"] == "done"

    response = client.post(
        "/retrieval/query", json={"query": "introduction", "top_k": 3}, headers=auth_headers
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert results
    assert results[0]["document_id"] == status_body["result"]["document_id"]
    assert 0 < results[0]["score"] <= 1.0

    reranked_response = client.post(
        "/retrieval/query",
        json={"query": "introduction", "top_k": 3, "rerank": True},
        headers=auth_headers,
    )
    assert reranked_response.status_code == 200
    reranked_results = reranked_response.json()["results"]
    assert reranked_results
    assert reranked_results[0]["document_id"] == status_body["result"]["document_id"]

    expanded_response = client.post(
        "/retrieval/query",
        json={"query": "introduction", "top_k": 3, "expand_sections": True},
        headers=auth_headers,
    )
    assert expanded_response.status_code == 200
    expanded_results = expanded_response.json()["results"]
    assert expanded_results
    assert expanded_results[0]["document_id"] == status_body["result"]["document_id"]


def _upload_and_wait(pdf_path, headers) -> str:
    with open(pdf_path, "rb") as pdf_file:
        upload = client.post(
            "/ingestion/pdf",
            files={"file": ("doc.pdf", pdf_file, "application/pdf")},
            headers=headers,
        )
    job_id = upload.json()["job_id"]
    deadline = time.monotonic() + 60.0
    status_body = None
    while time.monotonic() < deadline:
        status_body = client.get(f"/ingestion/jobs/{job_id}", headers=headers).json()
        if status_body["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert status_body is not None and status_body["status"] == "done"
    return status_body["result"]["document_id"]


def test_query_with_document_ids_restricts_to_that_document(simple_text_pdf, auth_headers):
    document_id_a = _upload_and_wait(simple_text_pdf, auth_headers)
    document_id_b = _upload_and_wait(simple_text_pdf, auth_headers)

    response = client.post(
        "/retrieval/query",
        json={"query": "introduction", "top_k": 10, "document_ids": [document_id_a]},
        headers=auth_headers,
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results
    assert all(r["document_id"] == document_id_a for r in results)
    assert all(r["document_id"] != document_id_b for r in results)


def test_query_with_empty_document_ids_returns_no_results(simple_text_pdf, auth_headers):
    _upload_and_wait(simple_text_pdf, auth_headers)

    response = client.post(
        "/retrieval/query",
        json={"query": "introduction", "document_ids": []},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"results": []}


def test_query_does_not_return_another_users_document(simple_text_pdf):
    owner_a_headers = _register_and_login("isolation-a")
    # ERP-116: cookie-based auth is client-scoped -- owner b needs its own TestClient, or
    # logging it in here would overwrite `client`'s cookies (owner a's session) before owner
    # a's own upload/poll calls below even run.
    other_client = TestClient(app)
    owner_b_headers = register_and_login(other_client, "isolation-b")

    with open(simple_text_pdf, "rb") as pdf_file:
        upload = client.post(
            "/ingestion/pdf",
            files={"file": ("simple.pdf", pdf_file, "application/pdf")},
            headers=owner_a_headers,
        )
    job_id = upload.json()["job_id"]

    deadline = time.monotonic() + 60.0
    status_body = None
    while time.monotonic() < deadline:
        status_response = client.get(f"/ingestion/jobs/{job_id}", headers=owner_a_headers)
        status_body = status_response.json()
        if status_body["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert status_body["status"] == "done"

    response = other_client.post(
        "/retrieval/query",
        json={"query": "introduction", "top_k": 3},
        headers=owner_b_headers,
    )
    assert response.status_code == 200
    assert response.json()["results"] == []
