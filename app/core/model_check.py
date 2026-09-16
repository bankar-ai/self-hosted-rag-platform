"""Verify that a configured Ollama model tag is actually installed.

Ollama only resolves an untagged model name (e.g. `"qwen3"`) to an implicit `:latest` tag -- it
does not fall back to whatever tag an operator happens to have pulled (e.g. `"qwen3:8b"`). A
mismatch here previously surfaced as a 500 on the first real user request; this module lets an
operator (or a deployment script) catch it before that, via `uv run python -m app.core.check_models`.
"""

import ollama


class ModelNotAvailableError(Exception):
    """Raised when a configured model tag is not present on the target Ollama host."""


def list_available_models(ollama_host: str) -> list[str]:
    """Return every model tag installed on the Ollama server at `ollama_host`."""
    response = ollama.Client(host=ollama_host).list()
    return [model.model for model in response.models if model.model is not None]


def _resolves_to(model: str, installed: str) -> bool:
    """Whether `model` (as passed to Ollama) would resolve to the exact installed tag `installed`.

    Mirrors Ollama's own behavior: an untagged name (no `:`) resolves to its `:latest` tag, so
    `"nomic-embed-text"` matches an installed `"nomic-embed-text:latest"` -- Ollama does NOT fall
    back to any other tag, so `"qwen3"` does not match an installed `"qwen3:8b"`.
    """
    if model == installed:
        return True
    return ":" not in model and installed == f"{model}:latest"


def check_model_available(ollama_host: str, model: str) -> bool:
    """Return whether `model` would resolve to an installed tag at `ollama_host` (see `_resolves_to`)."""
    return any(_resolves_to(model, installed) for installed in list_available_models(ollama_host))


def verify_model_or_raise(ollama_host: str, model: str, *, setting_name: str) -> None:
    """Raise `ModelNotAvailableError` with an actionable message if `model` isn't installed.

    `setting_name` (e.g. `"GENERATION_MODEL"`) is included in the error so an operator knows
    which env var to fix.
    """
    available = list_available_models(ollama_host)
    if any(_resolves_to(model, installed) for installed in available):
        return
    raise ModelNotAvailableError(
        f"{setting_name}={model!r} is not installed on Ollama at {ollama_host!r}. "
        f"Installed models: {available or '(none)'}. "
        f"Run `ollama pull {model}` on that host, or set {setting_name} to one of the "
        f"installed tags above -- Ollama does not fall back to a different tag automatically."
    )
