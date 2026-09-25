from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

_async_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_async_engine() -> AsyncEngine:
    global _async_engine
    if _async_engine is None:
        if settings.database_url is None:
            raise RuntimeError("DATABASE_URL must be configured to access the database")

        engine_kwargs: dict[str, Any] = {
            "pool_pre_ping": True,
            "pool_recycle": settings.db_pool_recycle_seconds,
        }
        if "sqlite" not in settings.database_url:
            engine_kwargs["pool_size"] = settings.db_pool_size
            engine_kwargs["max_overflow"] = settings.db_max_overflow
            engine_kwargs["pool_timeout"] = settings.db_pool_timeout_seconds

        _async_engine = create_async_engine(settings.database_url, **engine_kwargs)
    return _async_engine


async def check_database_connectivity(timeout_seconds: float = 3.0) -> bool:
    """Verifies database connectivity with strict timeout protection."""
    import asyncio

    from sqlalchemy import text

    try:
        engine = get_async_engine()

        async def _ping() -> bool:
            async with engine.connect() as conn:
                res = await conn.execute(text("SELECT 1"))
                return bool(res.scalar() == 1)

        return await asyncio.wait_for(_ping(), timeout=timeout_seconds)
    except Exception:
        return False


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_async_engine(), expire_on_commit=False)
    return _sessionmaker


def async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Factory alias for get_sessionmaker() to support background workers."""
    return get_sessionmaker()


async def get_async_session() -> AsyncIterator[AsyncSession]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


async def dispose_async_engine() -> None:
    global _async_engine, _sessionmaker
    if _async_engine is not None:
        await _async_engine.dispose()
        _async_engine = None
        _sessionmaker = None
