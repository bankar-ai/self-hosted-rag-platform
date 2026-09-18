# tests/ingestion/test_router.py
import io
import time

import pytest
from fastapi.testclient import TestClient

from app.embedding.client import OllamaEmbeddingClient
from app.embedding.config import get_embedding_settings
from app.ingestion.config import get_settings
from app.main import app
from tests.auth_helpers import register_and_login

client = TestClient(app)
_PDF_MAGIC = b"%PDF-"


@pytest.fixture
def auth_headers():
    return register_and_login(client, "ingestion")


@pytest.fixture(autouse=True)
def _stub_embedding_backend(monkeypatch, tmp_path):
    """Stub the Ollama call and redirect the FAISS index to a temp path for every test here.

    `POST /ingestion/pdf` schedules `run_ingestion_job` with its production defaults (no
    injected `embedding_client`/`faiss_index_store` — see `app/ingestion/router.py`), so
    without this, these end-to-end tests would make a real network call to Ollama
    (unavailable in CI, per the design's "no live Ollama required" testing intent) and write
    to the repo's real per-owner index directory.
    """
    monkeypatch.setenv("EMBEDDING_FAISS_INDEX_DIR", str(tmp_path / "router_test_index"))
    get_embedding_settings.cache_clear()

    def _fake_embed(self, texts):
        dimension = get_embedding_settings().dimension
        return [[0.1] * dimension for _ in texts]

    monkeypatch.setattr(OllamaEmbeddingClient, "embed", _fake_embed)
    try:
        yield
    finally:
        get_embedding_settings.cache_clear()


def _read_fixture_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _poll_until_done(job_id: str, auth_headers: dict, timeout_seconds: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/ingestion/jobs/{job_id}", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.1)
    raise TimeoutError(f"job {job_id} did not finish within {timeout_seconds}s")


def test_upload_pdf_rejects_non_pdf_content_type(auth_headers):
    response = client.post(
        "/ingestion/pdf",
        files={"file": ("notes.txt", io.BytesIO(b"just text"), "text/plain")},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_upload_pdf_rejects_file_without_pdf_header(auth_headers):
    response = client.post(
        "/ingestion/pdf",
        files={"file": ("fake.pdf", io.BytesIO(b"not really a pdf"), "application/pdf")},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_upload_pdf_rejects_missing_filename(auth_headers):
    # httpx's `files=` shorthand drops the `filename` param entirely when it's empty,
    # which makes the part look like a plain form field and 422s before reaching our
    # code. A raw multipart body with an explicit empty `filename=""` is required to
    # actually deliver an UploadFile with a falsy filename to the handler.
    body = (
        b"--boundary\r\n"
        b'Content-Disposition: form-data; name="file"; filename=""\r\n'
        b"Content-Type: application/pdf\r\n\r\n"
        + _PDF_MAGIC
        + b"rest\r\n"
        b"--boundary--\r\n"
    )
    response = client.post(
        "/ingestion/pdf",
        content=body,
        headers={**auth_headers, "Content-Type": "multipart/form-data; boundary=boundary"},
    )
    assert response.status_code == 400


def test_upload_and_poll_simple_pdf(simple_text_pdf, auth_headers):
    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    response = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    final = _poll_until_done(job_id, auth_headers)
    assert final["status"] == "done"
    assert final["result"]["chunks"]
    assert final["result"]["chunks"][0]["source_filename"] == "simple.pdf"


def test_upload_and_poll_multi_paragraph_pdf(multi_paragraph_pdf, auth_headers):
    # End-to-end regression test for a Critical whole-branch-review finding: every existing
    # fixture used single-line sections, which accidentally masked a chunker bug where
    # MarkdownHeaderTextSplitter's reconstructed (whitespace-normalized) section.page_content
    # broke substring-offset-based page tracking for any section spanning more than one line
    # — i.e. essentially all real PDFs. This exercises the full upload -> parse -> chunk
    # pipeline against a realistic multi-paragraph, multi-line fixture.
    pdf_bytes = _read_fixture_bytes(multi_paragraph_pdf)
    response = client.post(
        "/ingestion/pdf",
        files={"file": ("multi_paragraph.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    final = _poll_until_done(job_id, auth_headers)
    assert final["status"] == "done"
    chunks = final["result"]["chunks"]
    assert chunks

    combined = " ".join(c["text"] for c in chunks)
    assert "First paragraph of the intro" in combined
    assert "Second paragraph after a visual gap" in combined

    for chunk in chunks:
        assert chunk["text"].strip()
        assert chunk["section_path"] == ["Introduction"]
        assert chunk["page_start"] == 1
        assert chunk["page_end"] == 1
        # No splitter implementation artifacts should leak into chunk text.
        assert "  \n" not in chunk["text"]


def test_get_job_status_404_for_unknown_job(auth_headers):
    response = client.get("/ingestion/jobs/does-not-exist", headers=auth_headers)
    assert response.status_code == 404


def test_retry_job_reruns_a_failed_job_to_completion(monkeypatch, simple_text_pdf, auth_headers):
    # First call to ingest_pdf fails (simulating a transient failure); the retry's call
    # succeeds -- proving retry actually re-runs ingestion against the original bytes rather
    # than just flipping a status.
    import app.ingestion.jobs as jobs_module

    real_ingest_pdf = jobs_module.ingest_pdf
    calls = {"count": 0}

    def _flaky_ingest_pdf(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("simulated transient failure")
        return real_ingest_pdf(*args, **kwargs)

    monkeypatch.setattr(jobs_module, "ingest_pdf", _flaky_ingest_pdf)

    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    upload = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    job_id = upload.json()["job_id"]
    failed = _poll_until_done(job_id, auth_headers)
    assert failed["status"] == "failed"

    retry_response = client.post(f"/ingestion/jobs/{job_id}/retry", headers=auth_headers)
    assert retry_response.status_code == 202
    new_job_id = retry_response.json()["job_id"]
    assert new_job_id != job_id

    retried = _poll_until_done(new_job_id, auth_headers)
    assert retried["status"] == "done"
    assert calls["count"] == 2

    # The original failed job's own status is untouched.
    original = client.get(f"/ingestion/jobs/{job_id}", headers=auth_headers)
    assert original.json()["status"] == "failed"


def test_retry_job_404_for_unknown_job(auth_headers):
    response = client.post("/ingestion/jobs/does-not-exist/retry", headers=auth_headers)
    assert response.status_code == 404


def test_retry_job_404_for_a_job_that_is_not_failed(simple_text_pdf, auth_headers):
    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    upload = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    job_id = upload.json()["job_id"]
    done = _poll_until_done(job_id, auth_headers)
    assert done["status"] == "done"

    response = client.post(f"/ingestion/jobs/{job_id}/retry", headers=auth_headers)
    assert response.status_code == 404


def test_retry_job_404_for_job_belonging_to_another_user(monkeypatch, simple_text_pdf, auth_headers):
    import app.ingestion.jobs as jobs_module

    monkeypatch.setattr(
        jobs_module, "ingest_pdf", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    upload = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    job_id = upload.json()["job_id"]
    _poll_until_done(job_id, auth_headers)

    other_user_headers = register_and_login(client, "ingestion-retry-other-owner")
    response = client.post(f"/ingestion/jobs/{job_id}/retry", headers=other_user_headers)
    assert response.status_code == 404


def test_get_job_status_404_for_job_belonging_to_another_user(simple_text_pdf, auth_headers):
    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    response = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    _poll_until_done(job_id, auth_headers)

    other_user_headers = register_and_login(client, "ingestion-other-owner")
    response = client.get(f"/ingestion/jobs/{job_id}", headers=other_user_headers)
    assert response.status_code == 404


def test_list_documents_empty_for_new_user(auth_headers):
    response = client.get("/documents", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"documents": []}


def test_list_documents_returns_document_after_successful_ingestion(simple_text_pdf, auth_headers):
    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    upload = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    job_id = upload.json()["job_id"]
    final = _poll_until_done(job_id, auth_headers)
    document_id = final["result"]["document_id"]

    response = client.get("/documents", headers=auth_headers)

    assert response.status_code == 200
    documents = response.json()["documents"]
    assert any(d["document_id"] == document_id and d["filename"] == "simple.pdf" for d in documents)


def test_list_documents_does_not_include_another_users_documents(simple_text_pdf, auth_headers):
    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    upload = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    _poll_until_done(upload.json()["job_id"], auth_headers)

    other_user_headers = register_and_login(client, "ingestion-documents-other-owner")
    response = client.get("/documents", headers=other_user_headers)

    assert response.status_code == 200
    assert response.json() == {"documents": []}


def test_delete_document_removes_it_from_the_list(simple_text_pdf, auth_headers):
    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    upload = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    final = _poll_until_done(upload.json()["job_id"], auth_headers)
    document_id = final["result"]["document_id"]

    delete_response = client.delete(f"/documents/{document_id}", headers=auth_headers)
    assert delete_response.status_code == 204

    list_response = client.get("/documents", headers=auth_headers)
    assert all(d["document_id"] != document_id for d in list_response.json()["documents"])


def test_delete_document_404_for_unknown_document(auth_headers):
    response = client.delete("/documents/does-not-exist", headers=auth_headers)
    assert response.status_code == 404


def test_delete_document_404_for_document_belonging_to_another_user(simple_text_pdf, auth_headers):
    pdf_bytes = _read_fixture_bytes(simple_text_pdf)
    upload = client.post(
        "/ingestion/pdf",
        files={"file": ("simple.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers=auth_headers,
    )
    final = _poll_until_done(upload.json()["job_id"], auth_headers)
    document_id = final["result"]["document_id"]

    other_user_headers = register_and_login(client, "ingestion-delete-other-owner")
    response = client.delete(f"/documents/{document_id}", headers=other_user_headers)

    assert response.status_code == 404
    still_listed = client.get("/documents", headers=auth_headers)
    assert any(d["document_id"] == document_id for d in still_listed.json()["documents"])


def test_upload_pdf_rejects_oversized_file(monkeypatch, auth_headers):
    # Finding 3 (final whole-branch review): the upload endpoint streamed uploads of
    # unbounded size to a temp file with no size check — a resource-exhaustion risk.
    # A tiny configured limit lets this test exercise the guard without a huge fixture.
    monkeypatch.setenv("INGESTION_MAX_UPLOAD_SIZE_BYTES", "10")
    get_settings.cache_clear()
    try:
        oversized_body = _PDF_MAGIC + b"0" * 100
        response = client.post(
            "/ingestion/pdf",
            files={"file": ("big.pdf", io.BytesIO(oversized_body), "application/pdf")},
            headers=auth_headers,
        )
        assert response.status_code == 413
    finally:
        get_settings.cache_clear()
