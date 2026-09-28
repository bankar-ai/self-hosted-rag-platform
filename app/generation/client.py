"""Answer generation via a local Ollama chat model, or a hosted OpenRouter model (ERP-106)."""

import json
import logging
import time
from typing import Iterator, Protocol

import httpx
import ollama

from app.core.telemetry import get_meter, get_tracer
from app.generation.config import GenerationSettings
from app.generation.tracing import trace_generation

logger = logging.getLogger(__name__)

_duration_histogram = get_meter().create_histogram(
    "llm_generation_duration_seconds", description="Duration of a non-streaming LLM generation call"
)


class LLMClient(Protocol):
    """Anything that can turn a system+user prompt pair into an answer string."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Return the model's answer text for the given system/user prompts."""
        ...

    def generate_stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        """Yield the model's answer text in chunks, in order, for the given prompts."""
        ...

    def ping(self) -> None:
        """Best-effort warmup touch (ERP-091/ERP-106); a no-op for a provider with no cold start."""
        ...


class OllamaLLMClient:
    """`LLMClient` backed by a local Ollama chat model."""

    def __init__(self, settings: GenerationSettings) -> None:
        """Build a client bound to `settings.ollama_host`/`settings.model`/`settings.temperature`."""
        self._client = ollama.Client(host=settings.ollama_host)
        self._model = settings.model
        self._temperature = settings.temperature
        logger.info(
            "Generation LLM client configured for model=%r at %r -- this must match an "
            "installed Ollama tag exactly (run `ollama list`, or `uv run python -m "
            "app.core.check_models` to verify before deploying)",
            self._model,
            settings.ollama_host,
        )

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Send `system_prompt`/`user_prompt` to Ollama and return the response text."""
        with get_tracer().start_as_current_span("llm.generate") as span:
            span.set_attribute("llm.model", self._model)
            start = time.monotonic()
            with trace_generation(
                model=self._model, system_prompt=system_prompt, user_prompt=user_prompt
            ) as generation:
                response = self._client.chat(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    options={"temperature": self._temperature},
                    think=False,
                )
                text = response.message.content or ""
                usage_details = None
                if response.prompt_eval_count is not None and response.eval_count is not None:
                    usage_details = {
                        "input": response.prompt_eval_count,
                        "output": response.eval_count,
                    }
                generation.update(output=text, usage_details=usage_details)
            _duration_histogram.record(time.monotonic() - start, {"model": self._model})
            return text

    def ping(self) -> None:
        """Touch the Ollama server without generating anything (ERP-091).

        Listing local models is the cheapest real request Ollama supports -- used purely to
        trigger a scale-to-zero backend's (Modal) cold start ahead of an actual generation
        call, so the ~52-59s cold-start cost (ERP-037) overlaps with the user reading the page
        instead of landing entirely on their first real message.
        """
        self._client.list()

    def generate_stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        """Stream `system_prompt`/`user_prompt` to Ollama, yielding response text chunks in order."""
        with trace_generation(
            model=self._model, system_prompt=system_prompt, user_prompt=user_prompt
        ) as generation:
            stream = self._client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                options={"temperature": self._temperature},
                think=False,
                stream=True,
            )
            chunks: list[str] = []
            for chunk in stream:
                content = chunk.message.content
                if content:
                    chunks.append(content)
                    yield content
            generation.update(output="".join(chunks), usage_details=None)


class OpenRouterLLMClient:
    """`LLMClient` backed by OpenRouter's OpenAI-compatible hosted inference API (ERP-106).

    Unlike `OllamaLLMClient` (a self-hosted, Modal-backed model that scales to zero when idle),
    OpenRouter routes to always-on shared infrastructure -- there is no cold start to hide, so
    `ping()` is a documented no-op rather than a real warmup.
    """

    def __init__(self, settings: GenerationSettings) -> None:
        """Build a client bound to `settings.openrouter_*`. Raises if no API key is configured."""
        if not settings.openrouter_api_key:
            raise ValueError(
                "GENERATION_OPENROUTER_API_KEY is required when GENERATION_PROVIDER=openrouter"
            )
        self._client = httpx.Client(
            base_url=settings.openrouter_base_url,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            timeout=httpx.Timeout(60.0),
        )
        self._model = settings.openrouter_model
        self._temperature = settings.temperature
        logger.info("Generation LLM client configured for OpenRouter model=%r", self._model)

    def _messages(self, system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Send `system_prompt`/`user_prompt` to OpenRouter and return the response text."""
        with get_tracer().start_as_current_span("llm.generate") as span:
            span.set_attribute("llm.model", self._model)
            start = time.monotonic()
            with trace_generation(
                model=self._model, system_prompt=system_prompt, user_prompt=user_prompt
            ) as generation:
                response = self._client.post(
                    "/chat/completions",
                    json={
                        "model": self._model,
                        "messages": self._messages(system_prompt, user_prompt),
                        "temperature": self._temperature,
                    },
                )
                response.raise_for_status()
                body = response.json()
                text = body["choices"][0]["message"]["content"] or ""
                usage = body.get("usage")
                usage_details = None
                if usage and "prompt_tokens" in usage and "completion_tokens" in usage:
                    usage_details = {
                        "input": usage["prompt_tokens"],
                        "output": usage["completion_tokens"],
                    }
                generation.update(output=text, usage_details=usage_details)
            _duration_histogram.record(time.monotonic() - start, {"model": self._model})
            return text

    def ping(self) -> None:
        """No-op -- OpenRouter has no scale-to-zero cold start to hide (ERP-106)."""

    def generate_stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        """Stream `system_prompt`/`user_prompt` to OpenRouter, yielding text chunks in order.

        Parses the standard OpenAI-compatible SSE shape (`data: {...}` lines, terminated by a
        literal `data: [DONE]`) -- no extra dependency needed, `httpx`'s own line iteration is
        enough for this simple a format.
        """
        with (
            trace_generation(
                model=self._model, system_prompt=system_prompt, user_prompt=user_prompt
            ) as generation,
            self._client.stream(
                "POST",
                "/chat/completions",
                json={
                    "model": self._model,
                    "messages": self._messages(system_prompt, user_prompt),
                    "temperature": self._temperature,
                    "stream": True,
                },
            ) as response,
        ):
            response.raise_for_status()
            chunks: list[str] = []
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[len("data: ") :]
                if data == "[DONE]":
                    break
                delta = json.loads(data)["choices"][0]["delta"].get("content")
                if delta:
                    chunks.append(delta)
                    yield delta
            generation.update(output="".join(chunks), usage_details=None)


def get_default_llm_client(settings: GenerationSettings) -> "LLMClient":
    """Build the `LLMClient` configured by `settings.provider` (ERP-106).

    The single place that decides Ollama vs. OpenRouter -- every call site that previously
    default-constructed `OllamaLLMClient(settings)` directly goes through this instead, so
    switching providers is a one-place config change, not a find-and-replace.
    """
    if settings.provider == "openrouter":
        return OpenRouterLLMClient(settings)
    return OllamaLLMClient(settings)
