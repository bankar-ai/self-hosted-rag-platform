"""Answer generation via a local Ollama chat model."""

import logging
import time
from typing import Iterator, Protocol

import ollama

from app.core.telemetry import get_meter, get_tracer
from app.generation.config import GenerationSettings

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
            response = self._client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                options={"temperature": self._temperature},
                think=False,
            )
            _duration_histogram.record(time.monotonic() - start, {"model": self._model})
            return response.message.content or ""

    def generate_stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        """Stream `system_prompt`/`user_prompt` to Ollama, yielding response text chunks in order."""
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
        for chunk in stream:
            content = chunk.message.content
            if content:
                yield content
