import uuid

from app.core.rate_limit import RateLimiter, RateLimitSettings, get_default_rate_limiter


def test_requests_within_limit_are_allowed(rate_limit_settings):
    settings = rate_limit_settings.model_copy(update={"requests_per_minute": 3})
    limiter = RateLimiter(settings)
    owner_id = uuid.uuid4()
    for _ in range(3):
        allowed, retry_after = limiter.check(owner_id, "generation")
        assert allowed is True
        assert retry_after == 0


def test_request_past_limit_is_rejected_with_retry_after(rate_limit_settings):
    settings = rate_limit_settings.model_copy(update={"requests_per_minute": 2})
    limiter = RateLimiter(settings)
    owner_id = uuid.uuid4()
    limiter.check(owner_id, "generation")
    limiter.check(owner_id, "generation")
    allowed, retry_after = limiter.check(owner_id, "generation")
    assert allowed is False
    assert 0 < retry_after <= 60


def test_different_owners_have_independent_budgets(rate_limit_settings):
    settings = rate_limit_settings.model_copy(update={"requests_per_minute": 1})
    limiter = RateLimiter(settings)
    owner_a, owner_b = uuid.uuid4(), uuid.uuid4()
    assert limiter.check(owner_a, "generation")[0] is True
    assert limiter.check(owner_a, "generation")[0] is False
    assert limiter.check(owner_b, "generation")[0] is True


def test_different_scopes_have_independent_budgets(rate_limit_settings):
    settings = rate_limit_settings.model_copy(update={"requests_per_minute": 1})
    limiter = RateLimiter(settings)
    owner_id = uuid.uuid4()
    assert limiter.check(owner_id, "generation")[0] is True
    assert limiter.check(owner_id, "retrieval")[0] is True
    assert limiter.check(owner_id, "generation")[0] is False


def test_unreachable_redis_fails_open():
    settings = RateLimitSettings(redis_url="redis://localhost:1/0", requests_per_minute=1)
    limiter = RateLimiter(settings)
    allowed, retry_after = limiter.check(uuid.uuid4(), "generation")
    assert allowed is True
    assert retry_after == 0


def test_get_default_rate_limiter_is_memoized():
    get_default_rate_limiter.cache_clear()
    try:
        assert get_default_rate_limiter() is get_default_rate_limiter()
    finally:
        get_default_rate_limiter.cache_clear()
