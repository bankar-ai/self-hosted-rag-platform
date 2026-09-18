"""Ingestion settings, loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestionSettings(BaseSettings):
    """Configuration for the PDF ingestion pipeline, overridable via `INGESTION_*` env vars."""

    model_config = SettingsConfigDict(env_prefix="INGESTION_")

    chunk_size: int = 1500
    chunk_overlap: int = 200
    ocr_text_threshold: int = 20
    max_upload_size_bytes: int = 20_000_000
    docling_service_url: str | None = None
    docling_service_timeout_seconds: float = 480.0


@lru_cache
def get_settings() -> IngestionSettings:
    """Return the process-wide cached `IngestionSettings` instance."""
    return IngestionSettings()
