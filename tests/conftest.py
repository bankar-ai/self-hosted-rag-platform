"""Shared pytest fixtures: points every test run at a real test Postgres and manages schema."""

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/erp_test"
)
# Required by app.auth.config.AuthSettings (no default -- see that module's docstring).
# Set here, not just in tests/auth/conftest.py, so any test file can safely construct
# AuthSettings regardless of pytest's (alphabetical) collection order.
os.environ.setdefault("AUTH_JWT_SECRET_KEY", "test-only-secret-do-not-use-in-production")
# ERP-116: most router test files' TestClient uses plain http://testserver (not https://), but
# the access/refresh/csrf cookies default to Secure=True (production default) -- a Secure
# cookie is never sent back over plain HTTP, even by httpx's test client, which faithfully
# mirrors real browser behavior here. "false" switches to Secure=False/SameSite=Lax, the same
# local-dev mode a plain http://localhost deployment would use.
os.environ.setdefault("AUTH_COOKIE_SECURE", "false")
# Dedicated test-only Redis logical DB for the rate limiter (ERP-108) -- shared here (not just
# tests/core/conftest.py) since generation/retrieval router tests also need it.
os.environ.setdefault("RATE_LIMIT_REDIS_URL", "redis://localhost:6379/3")

import fitz  # noqa: E402
import pytest  # noqa: E402
import redis  # noqa: E402

from app.auth.models import RefreshTokenRecord, UserRecord  # noqa: E402, F401
from app.core.db import get_engine  # noqa: E402
from app.core.rate_limit import RateLimitSettings  # noqa: E402
from app.evaluation.models import EvaluationRunRecord  # noqa: E402, F401
from app.ingestion.models import Base  # noqa: E402


@pytest.fixture
def rate_limit_settings() -> RateLimitSettings:
    """`RateLimitSettings` pointed at the dedicated test Redis logical DB."""
    return RateLimitSettings(redis_url=os.environ["RATE_LIMIT_REDIS_URL"])


@pytest.fixture(autouse=True)
def _flush_rate_limit_redis_db(rate_limit_settings: RateLimitSettings):
    """Flush the rate limiter's test-only Redis logical DB before and after every test."""
    client = redis.Redis.from_url(rate_limit_settings.redis_url)
    client.flushdb()
    yield
    client.flushdb()


@pytest.fixture(scope="session", autouse=True)
def _database_schema():
    """Create all tables before the test session, drop them after."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def simple_text_pdf(tmp_path):
    """A one-page PDF with a heading and body text — should stay on the fast path.

    Shared at the repo root (rather than tests/ingestion/conftest.py) because the retrieval
    router's end-to-end test also needs it to exercise the full ingestion -> retrieval flow.
    """
    path = tmp_path / "simple.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Introduction", fontsize=18)
    page.insert_text(
        (72, 100), "This is a simple paragraph of body text for testing extraction.", fontsize=11
    )
    doc.save(str(path))
    doc.close()
    return str(path)
