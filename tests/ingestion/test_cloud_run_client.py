from unittest.mock import patch

import httpx
import pytest

from app.ingestion.cloud_run_client import (
    DoclingServiceError,
    call_docling_service,
    fetch_identity_token,
)
from app.ingestion.config import IngestionSettings

_RealHttpxClient = httpx.Client


def _stub_httpx_client(handler):
    """Fake all `httpx.Client(...)` calls through `handler`.

    Covers both the metadata-server GET and the service POST, mirroring
    `tests/auth/test_oidc.py`'s existing pattern for this repo.
    """
    return patch(
        "httpx.Client", lambda **kw: _RealHttpxClient(transport=httpx.MockTransport(handler), **kw)
    )


def _settings(**overrides):
    defaults = {
        "chunk_size": 1500,
        "chunk_overlap": 200,
        "ocr_text_threshold": 20,
        "docling_service_url": "https://docling-service.example.run.app",
        "docling_service_timeout_seconds": 5.0,
    }
    defaults.update(overrides)
    return IngestionSettings(**defaults)


def test_fetch_identity_token_requests_metadata_server_with_audience():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Metadata-Flavor"] == "Google"
        assert request.url.params["audience"] == "https://docling-service.example.run.app"
        return httpx.Response(200, text="fake-identity-token")

    with _stub_httpx_client(handler):
        token = fetch_identity_token("https://docling-service.example.run.app")

    assert token == "fake-identity-token"


def test_call_docling_service_posts_pdf_and_returns_parsed_pages_and_confidence(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-fake-content")

    def handler(request: httpx.Request) -> httpx.Response:
        if "identity" in str(request.url):
            return httpx.Response(200, text="fake-token")
        assert request.headers["Authorization"] == "Bearer fake-token"
        return httpx.Response(
            200, json={"pages": [{"text": "hello", "page_number": 1}], "confidence": "good"}
        )

    with _stub_httpx_client(handler):
        pages, confidence = call_docling_service(str(pdf_path), _settings())

    assert pages == [{"text": "hello", "page_number": 1}]
    assert confidence == "good"


def test_call_docling_service_raises_on_non_200_response(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-fake-content")

    def handler(request: httpx.Request) -> httpx.Response:
        if "identity" in str(request.url):
            return httpx.Response(200, text="fake-token")
        return httpx.Response(500, text="internal error")

    with _stub_httpx_client(handler):
        with pytest.raises(DoclingServiceError):
            call_docling_service(str(pdf_path), _settings())


def test_call_docling_service_raises_friendly_message_on_non_200_response(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-fake-content")

    def handler(request: httpx.Request) -> httpx.Response:
        if "identity" in str(request.url):
            return httpx.Response(200, text="fake-token")
        return httpx.Response(503, text="Service Unavailable")

    with _stub_httpx_client(handler):
        with pytest.raises(DoclingServiceError) as exc_info:
            call_docling_service(str(pdf_path), _settings())

    message = str(exc_info.value)
    assert "too large or complex" in message
    assert "Service Unavailable" not in message


def test_call_docling_service_raises_generic_message_on_4xx_response(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-fake-content")

    def handler(request: httpx.Request) -> httpx.Response:
        if "identity" in str(request.url):
            return httpx.Response(200, text="fake-token")
        return httpx.Response(404, text="Not Found")

    with _stub_httpx_client(handler):
        with pytest.raises(DoclingServiceError) as exc_info:
            call_docling_service(str(pdf_path), _settings())

    message = str(exc_info.value)
    assert "too large or complex" not in message
    assert "couldn't process this file" in message


def test_call_docling_service_raises_when_url_not_configured(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-fake-content")

    with pytest.raises(DoclingServiceError):
        call_docling_service(str(pdf_path), _settings(docling_service_url=None))
