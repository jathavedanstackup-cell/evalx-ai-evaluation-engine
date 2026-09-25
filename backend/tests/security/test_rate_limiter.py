import asyncio
import uuid

import pytest
from redis.asyncio import Redis

from app.core.config import settings
from app.security.rate_limiter import (
    InMemoryRateLimiter,
    RateLimiterUnavailableError,
    RateLimitTier,
    RedisRateLimiter,
)


@pytest.mark.asyncio
async def test_in_memory_rate_limiter_basic() -> None:
    limiter = InMemoryRateLimiter()
    key = f"test:rl:{uuid.uuid4()}"

    # 3 requests allowed with limit=3
    res1 = await limiter.acquire(key, limit=3, window_seconds=60)
    assert res1.allowed is True
    assert res1.remaining == 2

    res2 = await limiter.acquire(key, limit=3, window_seconds=60)
    assert res2.allowed is True
    assert res2.remaining == 1

    res3 = await limiter.acquire(key, limit=3, window_seconds=60)
    assert res3.allowed is True
    assert res3.remaining == 0

    # 4th request rejected
    res4 = await limiter.acquire(key, limit=3, window_seconds=60)
    assert res4.allowed is False
    assert res4.remaining == 0
    assert res4.retry_after > 0


@pytest.mark.asyncio
async def test_in_memory_rate_limiter_concurrency() -> None:
    limiter = InMemoryRateLimiter()
    key = f"test:rl:{uuid.uuid4()}"

    # 15 concurrent requests with limit 5
    tasks = [limiter.acquire(key, limit=5, window_seconds=60) for _ in range(15)]
    results = await asyncio.gather(*tasks)

    allowed_count = sum(1 for r in results if r.allowed)
    rejected_count = sum(1 for r in results if not r.allowed)

    assert allowed_count == 5
    assert rejected_count == 10


@pytest.mark.asyncio
async def test_tier_isolation() -> None:
    """Consuming one tier's quota must not exhaust another tier for the same user."""
    limiter = InMemoryRateLimiter()
    user_id = str(uuid.uuid4())

    runs_key = f"evalx:rl:runs:{user_id}"
    std_key = f"evalx:rl:std:{user_id}"

    # Exhaust runs tier (limit=2)
    await limiter.acquire(runs_key, limit=2, tier=RateLimitTier.RUNS)
    await limiter.acquire(runs_key, limit=2, tier=RateLimitTier.RUNS)
    rejected_run = await limiter.acquire(runs_key, limit=2, tier=RateLimitTier.RUNS)
    assert rejected_run.allowed is False

    # Standard tier for same user must still have its full quota available
    std_res = await limiter.acquire(std_key, limit=5, tier=RateLimitTier.STANDARD)
    assert std_res.allowed is True
    assert std_res.remaining == 4


@pytest.mark.asyncio
async def test_expensive_tier_redis_failure_fails_closed() -> None:
    """Expensive evaluation run tier must fail closed when Redis is unavailable."""
    limiter = InMemoryRateLimiter()
    limiter.simulate_redis_failure = True

    # RUNS tier default: fail-closed
    with pytest.raises(RateLimiterUnavailableError):
        await limiter.acquire(
            "evalx:rl:runs:user-1",
            limit=10,
            tier=RateLimitTier.RUNS,
        )

    # BULK tier default: fail-closed
    with pytest.raises(RateLimiterUnavailableError):
        await limiter.acquire(
            "evalx:rl:bulk:user-1",
            limit=20,
            tier=RateLimitTier.BULK,
        )


@pytest.mark.asyncio
async def test_standard_tier_redis_failure_fails_open() -> None:
    """Standard and public tiers fail open with degraded header when Redis is down."""
    limiter = InMemoryRateLimiter()
    limiter.simulate_redis_failure = True

    # STANDARD tier default: fail-open
    std_res = await limiter.acquire(
        "evalx:rl:std:user-1",
        limit=120,
        tier=RateLimitTier.STANDARD,
    )
    assert std_res.allowed is True
    assert std_res.degraded is True

    # PUBLIC tier default: fail-open
    pub_res = await limiter.acquire(
        "evalx:rl:pub:127.0.0.1",
        limit=60,
        tier=RateLimitTier.PUBLIC,
    )
    assert pub_res.allowed is True
    assert pub_res.degraded is True


@pytest.mark.asyncio
async def test_redis_lua_rate_limiter_atomicity() -> None:
    """Verifies atomic sliding-window Lua execution against real Redis if available."""
    try:
        redis_client = Redis.from_url(
            settings.redis_url, socket_timeout=1.0, socket_connect_timeout=1.0
        )
        await redis_client.ping()
    except Exception:
        pytest.skip("Local Redis server not available for Lua integration test")

    limiter = RedisRateLimiter(client=redis_client)
    test_key = f"evalx:rl:test:{uuid.uuid4().hex}"

    try:
        # Acquire 3 requests with limit 3
        res1 = await limiter.acquire(test_key, limit=3, window_seconds=10)
        assert res1.allowed is True
        assert res1.remaining == 2

        res2 = await limiter.acquire(test_key, limit=3, window_seconds=10)
        assert res2.allowed is True
        assert res2.remaining == 1

        res3 = await limiter.acquire(test_key, limit=3, window_seconds=10)
        assert res3.allowed is True
        assert res3.remaining == 0

        # 4th request must be atomically rejected
        res4 = await limiter.acquire(test_key, limit=3, window_seconds=10)
        assert res4.allowed is False
        assert res4.remaining == 0
        assert res4.retry_after >= 1

        # Test concurrent requests via Lua
        concurrent_key = f"evalx:rl:test:{uuid.uuid4().hex}"
        tasks = [
            limiter.acquire(concurrent_key, limit=5, window_seconds=10)
            for _ in range(12)
        ]
        results = await asyncio.gather(*tasks)

        allowed = sum(1 for r in results if r.allowed)
        rejected = sum(1 for r in results if not r.allowed)
        assert allowed == 5
        assert rejected == 7
    finally:
        await redis_client.delete(test_key)
        await redis_client.aclose()
