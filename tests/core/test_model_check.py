import pytest

from app.core.model_check import (
    ModelNotAvailableError,
    check_model_available,
    list_available_models,
    verify_model_or_raise,
)


class _FakeModel:
    def __init__(self, model: str):
        self.model = model


class _FakeListResponse:
    def __init__(self, models: list[str]):
        self.models = [_FakeModel(m) for m in models]


class _FakeOllamaClient:
    def __init__(self, host: str, installed: list[str]):
        self.host = host
        self._installed = installed

    def list(self):
        return _FakeListResponse(self._installed)


def test_list_available_models_returns_installed_tags(monkeypatch):
    monkeypatch.setattr(
        "app.core.model_check.ollama.Client",
        lambda host: _FakeOllamaClient(host, ["qwen3:8b", "nomic-embed-text:latest"]),
    )

    assert list_available_models("http://fake:11434") == ["qwen3:8b", "nomic-embed-text:latest"]


def test_check_model_available_true_when_installed(monkeypatch):
    monkeypatch.setattr(
        "app.core.model_check.ollama.Client",
        lambda host: _FakeOllamaClient(host, ["qwen3:8b"]),
    )

    assert check_model_available("http://fake:11434", "qwen3:8b") is True


def test_check_model_available_false_when_not_installed(monkeypatch):
    monkeypatch.setattr(
        "app.core.model_check.ollama.Client",
        lambda host: _FakeOllamaClient(host, ["qwen3:8b"]),
    )

    assert check_model_available("http://fake:11434", "qwen3") is False


def test_check_model_available_true_when_untagged_name_resolves_to_installed_latest(monkeypatch):
    """Mirrors Ollama's own resolution: an untagged name matches its installed `:latest` tag."""
    monkeypatch.setattr(
        "app.core.model_check.ollama.Client",
        lambda host: _FakeOllamaClient(host, ["nomic-embed-text:latest"]),
    )

    assert check_model_available("http://fake:11434", "nomic-embed-text") is True


def test_check_model_available_false_when_untagged_name_does_not_match_a_non_latest_tag(monkeypatch):
    """An untagged name must NOT match an arbitrary installed tag -- only `:latest`."""
    monkeypatch.setattr(
        "app.core.model_check.ollama.Client",
        lambda host: _FakeOllamaClient(host, ["qwen3:8b"]),
    )

    assert check_model_available("http://fake:11434", "qwen3") is False


def test_verify_model_or_raise_passes_silently_when_installed(monkeypatch):
    monkeypatch.setattr(
        "app.core.model_check.ollama.Client",
        lambda host: _FakeOllamaClient(host, ["qwen3:8b"]),
    )

    verify_model_or_raise("http://fake:11434", "qwen3:8b", setting_name="GENERATION_MODEL")


def test_verify_model_or_raise_raises_actionable_error_when_missing(monkeypatch):
    monkeypatch.setattr(
        "app.core.model_check.ollama.Client",
        lambda host: _FakeOllamaClient(host, ["qwen3:8b"]),
    )

    with pytest.raises(ModelNotAvailableError) as exc_info:
        verify_model_or_raise("http://fake:11434", "qwen3", setting_name="GENERATION_MODEL")

    message = str(exc_info.value)
    assert "GENERATION_MODEL" in message
    assert "qwen3" in message
    assert "qwen3:8b" in message
    assert "ollama pull" in message
