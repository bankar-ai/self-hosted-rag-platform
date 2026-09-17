from app.ingestion.config import IngestionSettings, get_settings


def test_default_settings():
    settings = IngestionSettings()
    assert settings.chunk_size == 1500
    assert settings.chunk_overlap == 200
    assert settings.ocr_text_threshold == 20


def test_settings_overridable_via_env(monkeypatch):
    monkeypatch.setenv("INGESTION_CHUNK_SIZE", "500")
    settings = IngestionSettings()
    assert settings.chunk_size == 500


def test_get_settings_returns_cached_instance():
    assert get_settings() is get_settings()


def test_docling_service_url_defaults_to_none():
    settings = IngestionSettings()
    assert settings.docling_service_url is None


def test_docling_service_timeout_defaults_to_480_seconds():
    settings = IngestionSettings()
    assert settings.docling_service_timeout_seconds == 480.0


def test_docling_service_url_overridable_via_env(monkeypatch):
    monkeypatch.setenv("INGESTION_DOCLING_SERVICE_URL", "https://example.run.app")
    settings = IngestionSettings()
    assert settings.docling_service_url == "https://example.run.app"
