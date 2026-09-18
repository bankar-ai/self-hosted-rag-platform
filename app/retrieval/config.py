"""Reranking settings, loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class RerankerSettings(BaseSettings):
    """Configuration for the optional cross-encoder reranking step.

    Overridable via `RERANKER_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="RERANKER_")

    model_name: str = "ms-marco-TinyBERT-L-2-v2"
    cache_dir: str = "data/reranker_cache"


@lru_cache
def get_reranker_settings() -> RerankerSettings:
    """Return the process-wide cached `RerankerSettings` instance."""
    return RerankerSettings()


class RetrievalSettings(BaseSettings):
    """Configuration for the Redis-backed retrieval/query-result cache.

    Overridable via `RETRIEVAL_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="RETRIEVAL_")

    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 300
    redis_socket_timeout_seconds: float = 2.0
    # ERP-046: a vector-leg relevance gate, not a cache setting -- grouped here since this is
    # already the retrieval-tuning settings class. RRF's fused score is rank-based, not a true
    # similarity measure, so it can't tell "great match" from "merely ranked highest among
    # irrelevant candidates" -- this filters candidates by the FAISS leg's raw L2 distance
    # *before* fusion. `None` disables the gate entirely. Calibrated live against the real
    # `nomic-embed-text` model: measured on-topic query distances of 0.74-0.85 against real
    # ingested content, vs. 1.02-1.15 for off-topic/greeting-like queries against the same
    # content -- 0.95 sits in the gap with margin on both sides (see `.ai/tickets/ERP-046.md`'s
    # Resolution for the exact measurements).
    max_relevant_distance: float | None = 0.95


@lru_cache
def get_retrieval_settings() -> RetrievalSettings:
    """Return the process-wide cached `RetrievalSettings` instance."""
    return RetrievalSettings()
