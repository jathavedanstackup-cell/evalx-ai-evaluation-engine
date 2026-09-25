import asyncio
import logging
import uuid
from typing import Any

from arq.connections import RedisSettings
from redis.asyncio import Redis

from app.core.config import settings
from app.database.session import async_session_factory
from app.worker.heartbeat import write_worker_heartbeat
from app.worker.tasks import execute_run_task

logger = logging.getLogger(__name__)


async def _heartbeat_loop(redis: Redis, worker_id: str, interval: int) -> None:
    """Periodically writes heartbeats to Redis until cancelled."""
    while True:
        try:
            await write_worker_heartbeat(redis, worker_id)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Heartbeat update failed: %s", exc)
        await asyncio.sleep(interval)


async def _scheduler_loop(session_factory: Any, interval: int) -> None:
    """Periodically polls and executes due evaluation schedules until cancelled."""
    from app.services.scheduler_service import poll_and_execute_due_schedules

    while True:
        try:
            async with session_factory() as session:
                await poll_and_execute_due_schedules(session)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Scheduler tick failed: %s", exc)
        await asyncio.sleep(interval)


async def startup(ctx: dict[str, Any]) -> None:
    """Initializes worker state, database session factory, and heartbeat."""
    worker_id = f"evalx-worker-{uuid.uuid4().hex[:8]}"
    ctx["worker_id"] = worker_id
    ctx["session_factory"] = async_session_factory

    redis = Redis.from_url(
        settings.redis_url,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
    )
    ctx["redis_client"] = redis

    # Initial heartbeat write
    await write_worker_heartbeat(redis, worker_id)

    # Reconcile any orphaned/stale runs from prior crashes
    try:
        sessionmaker = async_session_factory()
        async with sessionmaker() as session:
            from app.worker.reconciliation import reconcile_stale_runs

            reconciled = await reconcile_stale_runs(session)
            if reconciled > 0:
                logger.info(
                    "Worker startup: reconciled %d stale evaluation run(s)",
                    reconciled,
                )
    except Exception as exc:
        logger.warning("Worker startup: stale run reconciliation check failed: %s", exc)

    # Start background heartbeat refresher
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(
            redis=redis,
            worker_id=worker_id,
            interval=settings.worker_heartbeat_interval_seconds,
        )
    )
    ctx["heartbeat_task"] = heartbeat_task

    # Start background scheduler poller if enabled for this worker
    if settings.enable_scheduler_in_worker:
        scheduler_task = asyncio.create_task(
            _scheduler_loop(
                session_factory=async_session_factory,
                interval=settings.scheduler_poll_interval_seconds,
            )
        )
        ctx["scheduler_task"] = scheduler_task
    logger.info("EVALX worker %s started successfully", worker_id)


async def shutdown(ctx: dict[str, Any]) -> None:
    """Clean up background tasks, Redis client, and database pool."""
    task = ctx.get("heartbeat_task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    sched_task = ctx.get("scheduler_task")
    if sched_task and not sched_task.done():
        sched_task.cancel()
        try:
            await sched_task
        except asyncio.CancelledError:
            pass

    redis: Redis | None = ctx.get("redis_client")
    if redis is not None:
        await redis.aclose()

    from app.database.session import dispose_async_engine

    await dispose_async_engine()

    logger.info("EVALX worker %s shut down cleanly", ctx.get("worker_id", "unknown"))


class WorkerSettings:
    """ARQ Worker configuration settings."""

    functions = [execute_run_task]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = settings.worker_concurrency
    max_tries = settings.worker_max_retries
    job_timeout = 300
    on_startup = startup
    on_shutdown = shutdown
