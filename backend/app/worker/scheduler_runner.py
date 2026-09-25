"""Standalone scheduler runner service (Step 16).

Runs the recurring schedule polling loop as a dedicated service container,
enabling isolated scheduling without coupling to ARQ worker concurrency.
Handles SIGINT and SIGTERM gracefully for zero-downtime deployment restarts.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from app.core.config import settings
from app.database.session import async_session_factory, dispose_async_engine
from app.services.scheduler_service import poll_and_execute_due_schedules

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("evalx.scheduler")


async def run_scheduler(poll_interval: int | None = None) -> None:
    """Runs the scheduler polling loop until a termination signal is received."""
    interval = poll_interval or settings.scheduler_poll_interval_seconds
    stop_event = asyncio.Event()

    def _handle_signal(sig_name: str) -> None:
        logger.info(
            "Received signal %s, initiating graceful scheduler shutdown...",
            sig_name,
        )
        stop_event.set()

    loop = asyncio.get_running_loop()
    # Windows does not support add_signal_handler for SIGTERM
    if sys.platform != "win32":
        for s in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(s, lambda s=s: _handle_signal(s.name))

    logger.info(
        "EVALX Dedicated Scheduler Service started (poll_interval=%ds)", interval
    )
    session_factory = async_session_factory()

    try:
        while not stop_event.is_set():
            try:
                async with session_factory() as session:
                    executed = await poll_and_execute_due_schedules(session)
                    if executed > 0:
                        logger.info("Scheduler executed %d due schedule(s)", executed)
            except Exception as exc:
                logger.warning("Error in scheduler polling loop: %s", exc)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=float(interval))
            except TimeoutError:
                pass
    finally:
        logger.info("Cleaning up scheduler database connections...")
        await dispose_async_engine()
        logger.info("Scheduler service stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(run_scheduler())
    except KeyboardInterrupt, SystemExit:
        logger.info("Scheduler process terminated.")
