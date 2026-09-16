"""CLI entrypoint: `uv run python -m app.core.check_models`.

Fails fast, before deployment or before starting the app, if either configured Ollama model
(generation, embedding) doesn't match an actually-installed tag -- see `app/core/model_check.py`
and ERP-034. This module's stdout report is a deliberate exception to the no-`print()` rule, same
as `app.evaluation.run`/`app.evaluation.generation_run`.
"""

import sys

from app.core.model_check import ModelNotAvailableError, verify_model_or_raise
from app.embedding.config import get_embedding_settings
from app.generation.config import get_generation_settings


def main() -> None:
    """Check both configured models are installed on their configured Ollama hosts; exit(1) if not."""
    generation_settings = get_generation_settings()
    embedding_settings = get_embedding_settings()

    checks = [
        ("GENERATION_MODEL", generation_settings.ollama_host, generation_settings.model),
        ("EMBEDDING_MODEL", embedding_settings.ollama_host, embedding_settings.model),
    ]

    failures = []
    for setting_name, ollama_host, model in checks:
        try:
            verify_model_or_raise(ollama_host, model, setting_name=setting_name)
            print(f"OK   {setting_name}={model!r} is installed at {ollama_host!r}")
        except ModelNotAvailableError as exc:
            print(f"FAIL {exc}")
            failures.append(setting_name)

    if failures:
        print(f"\n{len(failures)} model(s) not available: {', '.join(failures)}")
        sys.exit(1)

    print("\nAll configured models are available.")


if __name__ == "__main__":
    main()
