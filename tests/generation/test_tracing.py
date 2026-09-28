import pytest

from app.generation import tracing


@pytest.fixture(autouse=True)
def _clear_langfuse_client_cache():
    """Each test controls credential availability independently."""
    tracing._get_langfuse_client.cache_clear()
    yield
    tracing._get_langfuse_client.cache_clear()


def test_trace_generation_is_a_noop_without_credentials(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    with tracing.trace_generation(
        model="test-model", system_prompt="system", user_prompt="user"
    ) as generation:
        generation.update(output="the answer")

    assert tracing._get_langfuse_client() is None


def test_get_langfuse_client_returns_client_when_credentials_present(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    sentinel = object()
    monkeypatch.setattr("langfuse.get_client", lambda: sentinel)

    assert tracing._get_langfuse_client() is sentinel


def test_get_langfuse_client_degrades_to_none_when_init_raises(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    def _raise():
        raise RuntimeError("network unreachable")

    monkeypatch.setattr("langfuse.get_client", _raise)

    assert tracing._get_langfuse_client() is None


class _FakeGenerationObservation:
    def __init__(self):
        self.updates = []

    def update(self, *, output, usage_details=None):
        self.updates.append({"output": output, "usage_details": usage_details})

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeLangfuseClient:
    def __init__(self):
        self.calls = []
        self.observation = _FakeGenerationObservation()

    def start_as_current_observation(self, *, as_type, name, model, input):
        self.calls.append({"as_type": as_type, "name": name, "model": model, "input": input})
        return self.observation


def test_trace_generation_wires_model_prompt_and_output_into_langfuse(monkeypatch):
    fake_client = _FakeLangfuseClient()
    monkeypatch.setattr(tracing, "_get_langfuse_client", lambda: fake_client)

    with tracing.trace_generation(
        model="test-model", system_prompt="system text", user_prompt="user text"
    ) as generation:
        generation.update(output="the final answer")

    assert fake_client.calls == [
        {
            "as_type": "generation",
            "name": "llm.generate",
            "model": "test-model",
            "input": {"system": "system text", "user": "user text"},
        }
    ]
    assert fake_client.observation.updates == [
        {"output": "the final answer", "usage_details": None}
    ]
