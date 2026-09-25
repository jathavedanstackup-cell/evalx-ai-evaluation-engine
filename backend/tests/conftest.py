import asyncio
import sys
import warnings
from collections.abc import Generator

import pytest

from app.main import app
from app.security.rate_limiter import InMemoryRateLimiter, get_rate_limiter

# On Windows, psycopg 3 async requires a Selector-based event loop.
if sys.platform == "win32":
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def override_rate_limiter() -> Generator[None]:
    """Ensures each test gets an isolated in-memory rate limiter by default."""
    limiter = InMemoryRateLimiter()
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    yield
    app.dependency_overrides.pop(get_rate_limiter, None)
