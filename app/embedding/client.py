"""Embedding generation via a local Ollama server, cache-aside in front of Redis."""

import logging
from typing import Protocol

import ollama

from app.core.telemetry import get_tracer
from app.embedding.cache import EmbeddingCache, get_default_embedding_cache
from app.embedding.config import EmbeddingSettings

logger = logging.getLogger(__name__)


class EmbeddingClient(Protocol):
    """Anything that can turn a batch of texts into embedding vectors."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, in the same order."""
        ...


class OllamaEmbeddingClient:
    """`EmbeddingClient` backed by Ollama, with a cache-aside lookup ahead of each call.

    Cache misses are batched into a single Ollama call and written back to the cache;
    cache hits never reach Ollama. Callers see no difference from a plain Ollama client.
    """

    def __init__(self, settings: EmbeddingSettings, cache: EmbeddingCache | None = None) -> None:
        """Build a client bound to `settings.ollama_host`/`settings.model`, cache-aside via `cache`.

        `cache` defaults to the process-wide memoized `RedisEmbeddingCache` when not given.
        """
        self._client = ollama.Client(host=settings.ollama_host)
        self._model = settings.model
        self._cache: EmbeddingCache = cache if cache is not None else get_default_embedding_cache()
        logger.info(
            "Embedding client configured for model=%r at %r -- this must match an installed "
            "Ollama tag exactly (run `ollama list`, or `uv run python -m app.core.check_models` "
            "to verify before deploying)",
            self._model,
            settings.ollama_host,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per text in `texts`, in the same order.

        Each text is looked up in the cache first; only cache misses are sent to Ollama,
        in a single batched call, and their results are written back to the cache.
        """
        with get_tracer().start_as_current_span("embedding.generate") as span:
            span.set_attribute("embedding.batch_size", len(texts))
            if not texts:
                return []

            vectors: list[list[float] | None] = [self._cache.get(self._model, text) for text in texts]
            miss_indices = [i for i, vector in enumerate(vectors) if vector is None]
            span.set_attribute("embedding.cache_misses", len(miss_indices))

            if miss_indices:
                miss_texts = [texts[i] for i in miss_indices]
                response = self._client.embed(model=self._model, input=miss_texts)
                new_vectors = list(response["embeddings"])
                for i, vector in zip(miss_indices, new_vectors, strict=True):
                    vectors[i] = vector
                    self._cache.set(self._model, texts[i], vector)

            return [vector for vector in vectors if vector is not None]
