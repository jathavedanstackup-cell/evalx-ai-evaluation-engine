import logging
from typing import Protocol
from uuid import UUID

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from redis.asyncio import Redis

from app.core.config import settings
from app.evaluation.errors import QueueUnavailableError, RunEnqueueError

logger = logging.getLogger(__name__)


class EvaluationQueue(Protocol):
    """Abstract queue interface for scheduling evaluation runs."""

    async def enqueue(self, run_id: UUID, correlation_id: str) -> str:
        """Enqueues an evaluation run job. Returns a unique job ID.

        Must raise RunEnqueueError or QueueUnavailableError on failure.
        """
        ...

    async def ping(self) -> bool:
        """Returns True if the queue broker is reachable, False otherwise."""
        ...

    async def close(self) -> None:
        """Gracefully closes underlying connections or connection pools."""
        ...


class RedisEvaluationQueue:
    """Production ARQ/Redis-backed queue for asynchronous evaluation runs."""

    def __init__(
        self,
        redis_url: str | None = None,
        pool: ArqRedis | None = None,
    ) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._pool: ArqRedis | None = pool
        self._redis_client: Redis | None = None

    async def _get_pool(self) -> ArqRedis:
        if self._pool is None:
            try:
                redis_settings = RedisSettings.from_dsn(self._redis_url)
                self._pool = await create_pool(redis_settings)
            except Exception as exc:
                logger.error("Failed to create ARQ Redis pool: %s", exc)
                raise QueueUnavailableError(
                    f"Redis queue unavailable at {self._redis_url}: {exc}"
                    f"Redis queue unavailable: {exc}"
                ) from exc
        return self._pool

    async def _get_client(self) -> Redis:
        if self._redis_client is None:
            self._redis_client = Redis.from_url(self._redis_url)
        return self._redis_client

    async def enqueue(self, run_id: UUID, correlation_id: str) -> str:
        """Enqueues the minimal job identifier payload to Redis via ARQ."""
        try:
            pool = await self._get_pool()
            # Minimal payload: only run_id and correlation_id. Never raw content.
            job = await pool.enqueue_job(
                "execute_run_task",
                str(run_id),
                correlation_id,
                _job_id=f"evalx-run-{run_id}",
            )
            if job is None:
                # Job with this _job_id is already in progress or queued
                return f"evalx-run-{run_id}"
            return job.job_id
        except QueueUnavailableError:
            raise
        except Exception as exc:
            logger.error("Failed to enqueue run %s: %s", run_id, exc)
            raise RunEnqueueError(
                f"Failed to enqueue evaluation run to worker queue: {exc}"
            ) from exc

    async def ping(self) -> bool:
        """Checks if Redis responds to PING."""
        try:
            client = await self._get_client()
            return bool(await client.ping())
        except Exception as exc:
            logger.warning("Redis ping failed: %s", exc)
            return False

    async def close(self) -> None:
        """Closes ARQ pool and direct Redis client."""
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None
        if self._redis_client is not None:
            await self._redis_client.aclose()
            self._redis_client = None


class InMemoryEvaluationQueue:
    """In-memory test queue implementation with zero external dependencies."""

    def __init__(self, should_fail: bool = False) -> None:
        self.enqueued_jobs: list[dict[str, str]] = []
        self.should_fail = should_fail
        self.is_closed = False

    @property
    def jobs(self) -> list[dict[str, str]]:
        return self.enqueued_jobs

    async def enqueue(self, run_id: UUID, correlation_id: str) -> str:
        if self.should_fail:
            raise QueueUnavailableError(
                "Simulated in-memory queue infrastructure failure"
            )
        job_id = f"evalx-job-{run_id}"
        self.enqueued_jobs.append(
            {
                "job_id": job_id,
                "run_id": str(run_id),
                "correlation_id": correlation_id,
            }
        )
        return job_id

    async def dequeue(self) -> dict[str, str] | None:
        if self.enqueued_jobs:
            return self.enqueued_jobs.pop(0)
        return None

    def clear(self) -> None:
        self.enqueued_jobs.clear()

    async def ping(self) -> bool:
        return not self.should_fail and not self.is_closed

    async def close(self) -> None:
        self.is_closed = True
