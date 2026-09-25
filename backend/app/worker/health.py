import logging
from typing import Any

from redis.asyncio import Redis

from app.core.config import settings
from app.worker.heartbeat import check_worker_heartbeat

logger = logging.getLogger(__name__)


async def check_worker_health(
    redis_client: Redis | None = None,
    redis_url: str | None = None,
) -> dict[str, Any]:
    """Inspects Redis availability and worker heartbeat freshness.

    Does not expose infrastructure secrets or credentials.
    """
    url = redis_url or settings.redis_url
    client = redis_client or Redis.from_url(url)
    should_close = redis_client is None

    redis_ok = False
    heartbeat_state = "unknown"
    details: dict[str, Any] = {}

    try:
        redis_ok = bool(await client.ping())
    except Exception as exc:
        logger.warning("Redis ping failed during health check: %s", exc)
        details["redis_error"] = str(exc)

    if redis_ok:
        try:
            worker_ok, hb_details = await check_worker_heartbeat(client)
            if worker_ok:
                heartbeat_state = "active"
                details.update(hb_details)
            else:
                heartbeat_state = "stale"
                details.update(hb_details)
        except Exception as exc:
            logger.warning("Heartbeat check failed: %s", exc)
            heartbeat_state = "unknown"
            details["heartbeat_error"] = str(exc)

    if should_close:
        await client.aclose()

    if redis_ok and heartbeat_state == "active":
        overall = "healthy"
    elif redis_ok:
        overall = "degraded"
    else:
        overall = "unhealthy"

    return {
        "status": overall,
        "redis_reachable": redis_ok,
        "worker_heartbeat": heartbeat_state,
        "details": details,
    }
