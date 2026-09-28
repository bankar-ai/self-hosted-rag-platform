"""Optional per-call LLM tracing via Langfuse Cloud (ERP-112).

Purely additive observability, never load-bearing -- same philosophy as this repo's Redis
caches (ADR-003) and OTel export (`app.core.telemetry`). Langfuse's Python SDK (v4) attaches its
own span processor to the process's existing global OpenTelemetry `TracerProvider`
(`app.core.telemetry.configure_telemetry` sets that up before any generation call can happen),
so a Langfuse "generation" observation shows up as a child of the existing `llm.generate` OTel
span rather than a second, disconnected trace.

If `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` aren't set (the default -- e.g. in CI), every call
here is a no-op, so `app.generation.client` never has to branch on whether Langfuse is
configured.
"""

import logging
import os
from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING, Iterator, Protocol

if TYPE_CHECKING:
    from langfuse import Langfuse


class GenerationObservation(Protocol):
    """The subset of Langfuse's generation-observation API this module relies on."""

    def update(self, *, output: str, usage_details: dict[str, int] | None = None) -> object:
        """Record the final output text and, if known, token usage for this generation."""
        ...


logger = logging.getLogger(__name__)


class _NoopObservation:
    """Stand-in used whenever Langfuse isn't configured or failed to initialize."""

    def update(self, *, output: str, usage_details: dict[str, int] | None = None) -> None:
        """Discard the update -- there is nothing to record."""


@lru_cache(maxsize=1)
def _get_langfuse_client() -> "Langfuse | None":
    """Build the Langfuse client once per process, or return `None` if unconfigured."""
    if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get("LANGFUSE_SECRET_KEY"):
        return None
    try:
        from langfuse import get_client

        return get_client()
    except Exception:
        logger.exception("Failed to initialize Langfuse client -- generation tracing disabled")
        return None


@contextmanager
def trace_generation(
    *, model: str, system_prompt: str, user_prompt: str
) -> Iterator[GenerationObservation]:
    """Wrap one LLM call in a Langfuse "generation" observation, or a no-op if unconfigured."""
    client = _get_langfuse_client()
    if client is None:
        yield _NoopObservation()
        return
    with client.start_as_current_observation(
        as_type="generation",
        name="llm.generate",
        model=model,
        input={"system": system_prompt, "user": user_prompt},
    ) as generation:
        yield generation
