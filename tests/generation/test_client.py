import json
from unittest.mock import patch

import httpx
import ollama
import pytest

from app.generation.client import OllamaLLMClient, OpenRouterLLMClient, get_default_llm_client
from app.generation.config import GenerationSettings

_RealHttpxClient = httpx.Client


def _stub_httpx_client(handler):
    """Fake all HTTP via `handler`, same pattern as tests/auth/test_oidc.py."""
    return patch(
        "httpx.Client", lambda **kw: _RealHttpxClient(transport=httpx.MockTransport(handler), **kw)
    )


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


def _openrouter_settings(**overrides) -> GenerationSettings:
    defaults = {
        "provider": "openrouter",
        "openrouter_api_key": "test-key",
        "openrouter_model": "test/model",
        "temperature": 0.2,
    }
    defaults.update(overrides)
    return GenerationSettings(**defaults)


def test_openrouter_generate_sends_openai_shaped_request_and_returns_content():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "the answer [1]"}}]})

    with _stub_httpx_client(handler):
        client = OpenRouterLLMClient(_openrouter_settings())
        answer = client.generate("system text", "user text")

    assert answer == "the answer [1]"
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"] == {
        "model": "test/model",
        "messages": [
            {"role": "system", "content": "system text"},
            {"role": "user", "content": "user text"},
        ],
        "temperature": 0.2,
    }


def test_openrouter_generate_raises_without_api_key():
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        OpenRouterLLMClient(_openrouter_settings(openrouter_api_key=None))


def test_openrouter_ping_is_a_noop():
    client = OpenRouterLLMClient(_openrouter_settings())
    assert client.ping() is None


def test_openrouter_generate_stream_yields_content_deltas_in_order():
    sse_body = (
        b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
        b'data: {"choices": [{"delta": {"content": " world"}}]}\n\n'
        b'data: {"choices": [{"delta": {}}]}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

    with _stub_httpx_client(handler):
        client = OpenRouterLLMClient(_openrouter_settings())
        chunks = list(client.generate_stream("system text", "user text"))

    assert chunks == ["Hello", " world"]


def test_get_default_llm_client_returns_ollama_client_by_default(monkeypatch):
    monkeypatch.setattr("app.generation.client.ollama.Client", lambda host: object())
    settings = GenerationSettings(provider="ollama")

    client = get_default_llm_client(settings)

    assert isinstance(client, OllamaLLMClient)


def test_get_default_llm_client_returns_openrouter_client_when_configured():
    client = get_default_llm_client(_openrouter_settings())

    assert isinstance(client, OpenRouterLLMClient)
