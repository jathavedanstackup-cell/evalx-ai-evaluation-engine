from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.session import get_async_session
from app.security.rate_limiter import rate_limit_public
from app.worker.health import check_worker_health

router = APIRouter(tags=["Health"], dependencies=[Depends(rate_limit_public)])


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/live")
def live_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready_check(
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict[str, str]:
    import asyncio

    # 1. Database check with timeout
    try:
        await asyncio.wait_for(
            session.execute(text("SELECT 1")),
            timeout=settings.health_check_timeout_seconds,
        )
    except TimeoutError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database timeout",
        ) from error
    except (OSError, SQLAlchemyError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from error

    # 2. Redis check with timeout
    try:

        async def _check_redis() -> None:
            redis = Redis.from_url(
                settings.redis_url,
                socket_timeout=settings.redis_socket_timeout_seconds,
                socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
            )
            try:
                await redis.ping()
            finally:
                await redis.aclose()

        await asyncio.wait_for(
            _check_redis(),
            timeout=settings.health_check_timeout_seconds,
        )
    except TimeoutError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis timeout",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis unavailable",
        ) from error

    return {"status": "ok"}


@router.get("/health/worker")
async def worker_health_check() -> dict[str, Any]:
    return await check_worker_health()
