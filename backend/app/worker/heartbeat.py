import json
import logging
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

WORKER_HEARTBEAT_KEY = "evalx:worker:heartbeat"


async def write_worker_heartbeat(redis: Redis, worker_id: str) -> None:
    """Writes a short-lived worker heartbeat key to Redis with an automatic TTL."""
    payload = json.dumps(
        {
            "worker_id": worker_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )
    ttl = settings.worker_heartbeat_ttl_seconds
    await redis.set(WORKER_HEARTBEAT_KEY, payload, ex=ttl)
    await redis.set(f"{WORKER_HEARTBEAT_KEY}:{worker_id}", payload, ex=ttl)


async def check_worker_heartbeat(redis: Redis) -> tuple[bool, dict[str, Any]]:
    """Checks whether an active, fresh worker heartbeat exists in Redis."""
    try:
        raw = await redis.get(WORKER_HEARTBEAT_KEY)
        if not raw:
            return False, {
                "error": "No worker heartbeat found (worker down or stopped)"
            }

        ttl = await redis.ttl(WORKER_HEARTBEAT_KEY)
        data = json.loads(raw)
        data["ttl_remaining_seconds"] = ttl
        return True, data
    except Exception as exc:
        logger.warning("Error reading worker heartbeat: %s", exc)
        return False, {"error": str(exc)}
