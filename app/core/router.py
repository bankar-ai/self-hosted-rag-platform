"""Health check -- deliberately unauthenticated (ERP-090/ERP-091)."""

import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.core.db import get_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Report liveness, touching Postgres so a DB outage shows as down, not just the API process.

    Deliberately unauthenticated: an external uptime check (ERP-090's planned Grafana Cloud
    Synthetic Monitoring) has no token to send, and the login page (ERP-091) fires this on
    mount -- before the user has authenticated -- purely to nudge Neon's scale-to-zero compute
    awake while they're still typing credentials, so the login request itself is less likely to
    pay the full cold-start cost.
    """
    try:
        with get_session_factory()() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Health check: database unreachable", exc_info=True)
        raise HTTPException(status_code=503, detail="Database unreachable") from exc
    return {"status": "ok"}
