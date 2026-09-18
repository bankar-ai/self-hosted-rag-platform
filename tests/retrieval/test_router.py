import time

import pytest
from fastapi.testclient import TestClient

from app.embedding.client import OllamaEmbeddingClient
from app.embedding.config import get_embedding_settings
from app.main import app
from tests.auth_helpers import register_and_login

client = TestClient(app)


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
    owner_b_headers = _register_and_login("isolation-b")

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

    response = client.post(
        "/retrieval/query",
        json={"query": "introduction", "top_k": 3},
        headers=owner_b_headers,
    )
    assert response.status_code == 200
    assert response.json()["results"] == []
