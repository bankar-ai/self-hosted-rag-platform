"""Generation settings, loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class GenerationSettings(BaseSettings):
    """Configuration for LLM-backed answer generation.

    Overridable via `GENERATION_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="GENERATION_")

    ollama_host: str = "http://localhost:11434"
    model: str = "qwen3"
    max_context_chars: int = 8000
    temperature: float = 0.1
    history_window_turns: int = 6
    # ERP-106: "ollama" (default, unchanged) talks to a self-hosted/Modal-backed model and pays
    # a real scale-to-zero cold-start cost; "openrouter" talks to always-on shared hosted
    # infrastructure via OpenRouter's OpenAI-compatible API, with no cold start to hide.
    provider: Literal["ollama", "openrouter"] = "ollama"
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemma-3-27b-it"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"


@lru_cache
def get_generation_settings() -> GenerationSettings:
    """Return the process-wide cached `GenerationSettings` instance."""
    return GenerationSettings()
