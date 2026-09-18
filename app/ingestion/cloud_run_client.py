"""HTTP client for the standalone Cloud Run docling-parsing service (ERP-047).

`docling` itself is never imported here or anywhere on the always-on VM -- its own documented
~4GB baseline memory requirement is why it moved to a separate service in the first place. See
docs/superpowers/specs/2026-09-17-docling-cloud-run-offload-design.md.
"""

import logging
from pathlib import Path
from typing import Any

import httpx

from app.ingestion.config import IngestionSettings

logger = logging.getLogger(__name__)

_METADATA_IDENTITY_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
)


class DoclingServiceError(Exception):
    """Raised when the Cloud Run docling service is unconfigured, unreachable, or errors."""


def fetch_identity_token(audience: str) -> str:
    """Fetch a short-lived GCP identity token scoped to `audience` from the metadata server.

    Only works when actually running on GCP (the VM) -- there is no local/CI fallback, and none
    is needed: tests fake the HTTP layer entirely rather than requiring a real metadata server.
    """
    with httpx.Client(timeout=5.0) as client:
        response = client.get(
            _METADATA_IDENTITY_URL,
            params={"audience": audience},
            headers={"Metadata-Flavor": "Google"},
        )
        response.raise_for_status()
        return response.text


def call_docling_service(pdf_path: str, settings: IngestionSettings) -> list[dict[str, Any]]:
    """Send the PDF at `pdf_path` to the Cloud Run docling service, returning its parsed pages.

    Raises `DoclingServiceError` if `settings.docling_service_url` isn't configured, the request
    times out, or the service returns a non-2xx response.
    """
    if not settings.docling_service_url:
        raise DoclingServiceError("INGESTION_DOCLING_SERVICE_URL is not configured")

    try:
        token = fetch_identity_token(settings.docling_service_url)
        pdf_bytes = Path(pdf_path).read_bytes()
        with httpx.Client(timeout=settings.docling_service_timeout_seconds) as client:
            response = client.post(
                f"{settings.docling_service_url}/parse",
                files={"file": (Path(pdf_path).name, pdf_bytes, "application/pdf")},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            result: list[dict[str, Any]] = response.json()
            return result
    except httpx.HTTPError as exc:
        logger.exception("Docling Cloud Run service call failed")
        raise DoclingServiceError(str(exc)) from exc
