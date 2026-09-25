import ollama

from app.generation.client import OllamaLLMClient
from app.generation.config import GenerationSettings


class _FakeOllamaClient:
    def __init__(self, host):
        self.host = host
        self.calls = []

    def chat(self, model, messages, options, think=None):
        self.calls.append((model, messages, options, think))
        return ollama.ChatResponse(
            message=ollama.Message(role="assistant", content="the answer [1]")
        )


def test_generate_calls_ollama_with_system_and_user_messages(monkeypatch):
    fake = _FakeOllamaClient(host="http://fake:11434")
    monkeypatch.setattr("app.generation.client.ollama.Client", lambda host: fake)
    settings = GenerationSettings(
        ollama_host="http://fake:11434", model="test-model", temperature=0.2
    )

    client = OllamaLLMClient(settings)
    answer = client.generate("system text", "user text")

    assert answer == "the answer [1]"
    assert fake.calls == [
        (
            "test-model",
            [
                {"role": "system", "content": "system text"},
                {"role": "user", "content": "user text"},
            ],
            {"temperature": 0.2},
            False,
        )
    ]


def test_ping_calls_list_and_returns_nothing(monkeypatch):
    class _FakeListingOllamaClient:
        def __init__(self, host):
            self.host = host
            self.list_calls = 0

        def list(self):
            self.list_calls += 1
            return {"models": []}

    fake = _FakeListingOllamaClient(host="http://fake:11434")
    monkeypatch.setattr("app.generation.client.ollama.Client", lambda host: fake)
    settings = GenerationSettings(ollama_host="http://fake:11434", model="test-model")

    client = OllamaLLMClient(settings)
    result = client.ping()

    assert result is None
    assert fake.list_calls == 1


def test_generate_stream_calls_ollama_with_stream_true_and_yields_content(monkeypatch):
    class _FakeStreamingOllamaClient:
        def __init__(self, host):
            self.host = host
            self.calls = []

        def chat(self, model, messages, options, think=None, stream=False):
            self.calls.append((model, messages, options, think, stream))
            return iter(
                [
                    ollama.ChatResponse(message=ollama.Message(role="assistant", content="Hello")),
                    ollama.ChatResponse(message=ollama.Message(role="assistant", content=" world")),
                    ollama.ChatResponse(message=ollama.Message(role="assistant", content=None)),
                ]
            )

    fake = _FakeStreamingOllamaClient(host="http://fake:11434")
    monkeypatch.setattr("app.generation.client.ollama.Client", lambda host: fake)
    settings = GenerationSettings(
        ollama_host="http://fake:11434", model="test-model", temperature=0.2
    )

    client = OllamaLLMClient(settings)
    chunks = list(client.generate_stream("system text", "user text"))

    assert chunks == ["Hello", " world"]
    assert fake.calls == [
        (
            "test-model",
            [
                {"role": "system", "content": "system text"},
                {"role": "user", "content": "user text"},
            ],
            {"temperature": 0.2},
            False,
            True,
        )
    ]
