"""Health check -- deliberately unauthenticated (ERP-090/ERP-091)."""

import logging

import redis
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.core.db import get_session_factory
from app.embedding.config import get_embedding_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Report liveness, touching Postgres and Redis so either being down shows here.

    Not just as a failure buried in the next request that happens to need one of them.
    Deliberately unauthenticated: an external uptime check (ERP-090's Grafana Cloud Synthetic
    Monitoring) has no token to send, and the login page (ERP-091) fires this on mount -- before
    the user has authenticated -- purely to nudge Neon's scale-to-zero compute awake while
    they're still typing credentials, so the login request itself is less likely to pay the
    full cold-start cost. Checks Redis via `EmbeddingSettings.redis_url` specifically -- all
    three `*_REDIS_URL` settings point at the same Upstash instance in this deployment (see
    `docs/deployment.md`), so any one of them is representative of Redis's own reachability.
    """
    try:
        with get_session_factory()() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Health check: database unreachable", exc_info=True)
        raise HTTPException(status_code=503, detail="Database unreachable") from exc

    try:
        redis.Redis.from_url(get_embedding_settings().redis_url, socket_connect_timeout=2.0).ping()
    except redis.RedisError as exc:
        logger.warning("Health check: redis unreachable", exc_info=True)
        raise HTTPException(status_code=503, detail="Redis unreachable") from exc

    return {"status": "ok"}
