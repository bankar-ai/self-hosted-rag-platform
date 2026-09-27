"""Per-user rate limiting for cost-exposed endpoints (ERP-108).

Every real generation call costs actual money (Modal today, optionally OpenRouter per
ERP-106), so an unthrottled `/generation/query`, `/generation/query/stream`, or
`/retrieval/query` is a direct cost-exposure vector, not just a nuisance-prevention gap.

Fixed-window counter backed by Redis (`INCR` + `EXPIRE`), keyed per user per calendar minute
-- simple, no extra infrastructure (reuses the same Redis already used for the embedding and
retrieval caches). Unlike those caches, a rate limiter is a safety control, not a
performance optimization, but it still **fails open** on a Redis error: an unreachable Redis
must not take down every generation/retrieval request for every user, and an attacker who can
also take down Redis has bigger problems than this limiter would have stopped anyway. Every
fail-open is logged so an outage is still visible.
"""

import logging
import time
import uuid
from functools import lru_cache

import redis
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class RateLimitSettings(BaseSettings):
    """Configuration for the per-user rate limiter.

    Overridable via `RATE_LIMIT_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="RATE_LIMIT_")

    redis_url: str = "redis://localhost:6379/0"
    redis_socket_timeout_seconds: float = 2.0
    requests_per_minute: int = 20


@lru_cache
def get_rate_limit_settings() -> RateLimitSettings:
    """Return the process-wide cached `RateLimitSettings` instance."""
    return RateLimitSettings()


class RateLimiter:
    """Fixed-window (1 minute) per-`(owner_id, scope)` request counter, backed by Redis."""

    def __init__(self, settings: RateLimitSettings) -> None:
        """Build a limiter bound to `settings.redis_url`, budget from `settings.requests_per_minute`."""
        self._client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        self._limit = settings.requests_per_minute

    def check(self, owner_id: uuid.UUID, scope: str) -> tuple[bool, int]:
        """Return `(allowed, retry_after_seconds)` for one call to `scope` by `owner_id`.

        `retry_after_seconds` is the number of seconds until the current window rolls over --
        meaningful only when `allowed` is `False`. A Redis error fails open (`True, 0`),
        logged as a warning rather than raising, so an outage degrades to "no rate limiting"
        rather than "nothing works".
        """
        now = time.time()
        window = int(now // 60)
        key = f"ratelimit:{scope}:{owner_id}:{window}"
        try:
            count = self._client.incr(key)
            if count == 1:
                self._client.expire(key, 60)
        except redis.RedisError:
            logger.warning(
                "Redis rate-limit check failed; failing open (allowing the request)", exc_info=True
            )
            return True, 0

        if count > self._limit:
            retry_after = 60 - int(now % 60)
            return False, retry_after
        return True, 0


@lru_cache
def get_default_rate_limiter() -> RateLimiter:
    """Return the process-wide cached default `RateLimiter`, reusing one Redis connection pool."""
    return RateLimiter(get_rate_limit_settings())
