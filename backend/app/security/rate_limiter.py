import asyncio
import logging
import math
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Protocol

from fastapi import Depends, Request, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.auth.dependencies import get_current_user
from app.core.config import settings
from app.models.user import User
from app.security.client_ip import get_client_ip

logger = logging.getLogger(__name__)

# Atomic Redis Lua script for sliding window rate limiting
SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

local window_start = now - window

-- 1. Prune expired members older than window_start
redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

-- 2. Count active requests in current window
local count = redis.call('ZCARD', key)

local allowed = 0
local remaining = 0
local retry_after = 0

if count < limit then
    -- Allowed: record current request
    allowed = 1
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, math.ceil(window + 5))
    remaining = limit - count - 1
else
    -- Rejected: calculate retry_after from earliest request in window
    allowed = 0
    remaining = 0
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    if oldest and #oldest >= 2 then
        local oldest_score = tonumber(oldest[2])
        local diff = (oldest_score + window) - now
        if diff > 0 then
            retry_after = math.ceil(diff)
        else
            retry_after = 1
        end
    else
        retry_after = 1
    end
    redis.call('EXPIRE', key, math.ceil(window + 5))
end

local reset = math.ceil(now + window)
return {allowed, limit, remaining, retry_after, reset}
"""


class RateLimitTier(StrEnum):
    STANDARD = "std"
    RUNS = "runs"
    BULK = "bulk"
    PUBLIC = "pub"


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    reset: int
    degraded: bool = False


class RateLimitExceededError(Exception):
    """Raised when request rate limit is exceeded."""

    def __init__(self, result: RateLimitResult) -> None:
        self.result = result
        super().__init__(
            f"Rate limit exceeded. Retry after {result.retry_after} seconds."
        )


class RateLimiterUnavailableError(Exception):
    """Raised when rate limiter backend fails and tier fails closed."""

    def __init__(self, tier: RateLimitTier, message: str) -> None:
        self.tier = tier
        super().__init__(message)


class RateLimiter(Protocol):
    """Abstract interface for rate limiting implementations."""

    async def acquire(
        self,
        key: str,
        limit: int,
        window_seconds: int = 60,
        tier: RateLimitTier = RateLimitTier.STANDARD,
        correlation_id: str | None = None,
    ) -> RateLimitResult: ...

    async def close(self) -> None: ...


class RedisRateLimiter:
    """Atomic Redis Lua sliding-window rate limiter with fail-open."""

    def __init__(
        self,
        redis_url: str | None = None,
        client: Redis | None = None,
    ) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._client: Redis | None = client
        self._script = None

    async def _get_client(self) -> Redis:
        try:
            current_loop = asyncio.get_running_loop()
            if self._client is not None:
                pool = getattr(self._client, "connection_pool", None)
                pool_loop = getattr(pool, "_loop", None)
                if pool_loop is not None and pool_loop != current_loop:
                    self._client = None
        except RuntimeError:
            pass

        if self._client is None:
            self._client = Redis.from_url(self._redis_url, decode_responses=False)
        return self._client

    async def acquire(
        self,
        key: str,
        limit: int,
        window_seconds: int = 60,
        tier: RateLimitTier = RateLimitTier.STANDARD,
        correlation_id: str | None = None,
    ) -> RateLimitResult:
        now = time.time()
        member_id = f"{now}-{uuid.uuid4().hex[:8]}"

        try:
            client = await self._get_client()
            res = await client.eval(  # type: ignore[misc,no-untyped-call]
                SLIDING_WINDOW_LUA,
                1,
                key,
                str(now),
                str(window_seconds),
                str(limit),
                member_id,
            )
            # res is [allowed, limit, remaining, retry_after, reset]
            return RateLimitResult(
                allowed=bool(res[0]),
                limit=int(res[1]),
                remaining=int(res[2]),
                retry_after=int(res[3]),
                reset=int(res[4]),
                degraded=False,
            )
        except (RedisError, ConnectionError, OSError, RuntimeError) as exc:
            self._client = None
            fail_open = self._is_tier_fail_open(tier)
            if fail_open:
                logger.warning(
                    "Rate limiter Redis unavailable; failing open for "
                    "tier=%s correlation_id=%s: %s",
                    tier.value,
                    correlation_id,
                    exc,
                )
                return RateLimitResult(
                    allowed=True,
                    limit=limit,
                    remaining=limit,
                    retry_after=0,
                    reset=math.ceil(now + window_seconds),
                    degraded=True,
                )
            else:
                logger.error(
                    "Rate limiter Redis unavailable; failing closed for "
                    "tier=%s correlation_id=%s: %s",
                    tier.value,
                    correlation_id,
                    exc,
                )
                raise RateLimiterUnavailableError(
                    tier=tier,
                    message="Rate limiter service is currently unavailable.",
                ) from exc

    def _is_tier_fail_open(self, tier: RateLimitTier) -> bool:
        if tier == RateLimitTier.STANDARD:
            return settings.rate_limit_fail_open_standard
        elif tier == RateLimitTier.PUBLIC:
            return settings.rate_limit_fail_open_public
        elif tier == RateLimitTier.RUNS:
            return settings.rate_limit_fail_open_runs
        elif tier == RateLimitTier.BULK:
            return settings.rate_limit_fail_open_bulk
        return True

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class InMemoryRateLimiter:
    """In-memory sliding-window rate limiter for deterministic test execution."""

    def __init__(self) -> None:
        self._storage: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()
        self.simulate_redis_failure: bool = False

    async def acquire(
        self,
        key: str,
        limit: int,
        window_seconds: int = 60,
        tier: RateLimitTier = RateLimitTier.STANDARD,
        correlation_id: str | None = None,
    ) -> RateLimitResult:
        now = time.time()

        if self.simulate_redis_failure:
            fail_open = self._is_tier_fail_open(tier)
            if fail_open:
                logger.warning(
                    "Simulated Redis failure: failing open for tier=%s "
                    "correlation_id=%s",
                    tier.value,
                    correlation_id,
                )
                return RateLimitResult(
                    allowed=True,
                    limit=limit,
                    remaining=limit,
                    retry_after=0,
                    reset=math.ceil(now + window_seconds),
                    degraded=True,
                )
            else:
                logger.error(
                    "Simulated Redis failure: failing closed for tier=%s "
                    "correlation_id=%s",
                    tier.value,
                    correlation_id,
                )
                raise RateLimiterUnavailableError(
                    tier=tier,
                    message="Rate limiter service is currently unavailable.",
                )

        async with self._lock:
            window_start = now - window_seconds
            timestamps = [ts for ts in self._storage.get(key, []) if ts > window_start]
            count = len(timestamps)

            if count < limit:
                timestamps.append(now)
                self._storage[key] = timestamps
                return RateLimitResult(
                    allowed=True,
                    limit=limit,
                    remaining=limit - count - 1,
                    retry_after=0,
                    reset=math.ceil(now + window_seconds),
                    degraded=False,
                )
            else:
                self._storage[key] = timestamps
                oldest = timestamps[0] if timestamps else now
                diff = (oldest + window_seconds) - now
                retry_after = max(1, math.ceil(diff))
                return RateLimitResult(
                    allowed=False,
                    limit=limit,
                    remaining=0,
                    retry_after=retry_after,
                    reset=math.ceil(now + window_seconds),
                    degraded=False,
                )

    def _is_tier_fail_open(self, tier: RateLimitTier) -> bool:
        if tier == RateLimitTier.STANDARD:
            return settings.rate_limit_fail_open_standard
        elif tier == RateLimitTier.PUBLIC:
            return settings.rate_limit_fail_open_public
        elif tier == RateLimitTier.RUNS:
            return settings.rate_limit_fail_open_runs
        elif tier == RateLimitTier.BULK:
            return settings.rate_limit_fail_open_bulk
        return True

    async def close(self) -> None:
        self._storage.clear()


def get_rate_limiter() -> RateLimiter:
    """FastAPI dependency provider for RateLimiter instance."""
    return RedisRateLimiter()


def apply_rate_limit_headers(response: Response, result: RateLimitResult) -> None:
    """Applies standardized rate-limiting headers to HTTP response."""
    response.headers["X-RateLimit-Limit"] = str(result.limit)
    response.headers["X-RateLimit-Remaining"] = str(result.remaining)
    response.headers["X-RateLimit-Reset"] = str(result.reset)
    if result.degraded:
        response.headers["X-RateLimit-Degraded"] = "true"
    if not result.allowed:
        response.headers["Retry-After"] = str(result.retry_after)


async def rate_limit_standard(
    request: Request,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> None:
    """Rate limits standard authenticated API requests per user."""
    if not settings.rate_limit_enabled:
        return

    cid = getattr(request.state, "correlation_id", None)
    key = f"evalx:rl:std:{current_user.id}"
    result = await limiter.acquire(
        key=key,
        limit=settings.rate_limit_standard_per_minute,
        window_seconds=60,
        tier=RateLimitTier.STANDARD,
        correlation_id=cid,
    )
    apply_rate_limit_headers(response, result)
    if not result.allowed:
        raise RateLimitExceededError(result)


async def rate_limit_runs(
    request: Request,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> None:
    """Rate limits expensive evaluation run creation per user."""
    if not settings.rate_limit_enabled:
        return

    cid = getattr(request.state, "correlation_id", None)
    key = f"evalx:rl:runs:{current_user.id}"
    result = await limiter.acquire(
        key=key,
        limit=settings.rate_limit_runs_per_minute,
        window_seconds=60,
        tier=RateLimitTier.RUNS,
        correlation_id=cid,
    )
    apply_rate_limit_headers(response, result)
    if not result.allowed:
        raise RateLimitExceededError(result)


async def rate_limit_bulk(
    request: Request,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> None:
    """Rate limits bulk dataset case ingestion per user."""
    if not settings.rate_limit_enabled:
        return

    cid = getattr(request.state, "correlation_id", None)
    key = f"evalx:rl:bulk:{current_user.id}"
    result = await limiter.acquire(
        key=key,
        limit=settings.rate_limit_bulk_per_minute,
        window_seconds=60,
        tier=RateLimitTier.BULK,
        correlation_id=cid,
    )
    apply_rate_limit_headers(response, result)
    if not result.allowed:
        raise RateLimitExceededError(result)


async def rate_limit_public(
    request: Request,
    response: Response,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> None:
    """Rate limits unauthenticated public endpoints by client IP."""
    if not settings.rate_limit_enabled:
        return

    cid = getattr(request.state, "correlation_id", None)
    client_ip = get_client_ip(request, settings.trusted_proxies_list)
    key = f"evalx:rl:pub:{client_ip}"
    result = await limiter.acquire(
        key=key,
        limit=settings.rate_limit_unauthenticated_per_minute,
        window_seconds=60,
        tier=RateLimitTier.PUBLIC,
        correlation_id=cid,
    )
    apply_rate_limit_headers(response, result)
    if not result.allowed:
        raise RateLimitExceededError(result)
