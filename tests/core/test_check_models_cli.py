import pytest

from app.core import check_models


class _FakeSettings:
    def __init__(self, ollama_host: str, model: str):
        self.ollama_host = ollama_host
        self.model = model


def test_main_exits_zero_when_both_models_available(monkeypatch, capsys):
    monkeypatch.setattr(
        check_models, "get_generation_settings", lambda: _FakeSettings("http://fake:11434", "qwen3:8b")
    )
    monkeypatch.setattr(
        check_models,
        "get_embedding_settings",
        lambda: _FakeSettings("http://fake:11434", "nomic-embed-text:latest"),
    )
    monkeypatch.setattr(check_models, "verify_model_or_raise", lambda *a, **k: None)

    check_models.main()

    assert "All configured models are available." in capsys.readouterr().out


def test_main_exits_one_when_a_model_is_missing(monkeypatch, capsys):
    from app.core.model_check import ModelNotAvailableError

    monkeypatch.setattr(
        check_models, "get_generation_settings", lambda: _FakeSettings("http://fake:11434", "qwen3")
    )
    monkeypatch.setattr(
        check_models,
        "get_embedding_settings",
        lambda: _FakeSettings("http://fake:11434", "nomic-embed-text:latest"),
    )

    def _fake_verify(host, model, *, setting_name):
        if setting_name == "GENERATION_MODEL":
            raise ModelNotAvailableError(f"{setting_name}={model!r} not found")

    monkeypatch.setattr(check_models, "verify_model_or_raise", _fake_verify)

    with pytest.raises(SystemExit) as exc_info:
        check_models.main()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "FAIL" in output
    assert "GENERATION_MODEL" in output
