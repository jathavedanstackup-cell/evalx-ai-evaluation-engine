import pytest
from httpx import ASGITransport, AsyncClient

from app.database.session import get_async_session
from app.main import app


@pytest.mark.asyncio
async def test_root() -> None:
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "EVALX",
        "status": "running",
        "version": "0.1.0",
    }


@pytest.mark.asyncio
async def test_health() -> None:
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_live_health_does_not_require_database() -> None:
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_health_returns_ok_when_database_is_available() -> None:
    class HealthySession:
        async def execute(self, _statement: object) -> None:
            return None

    async def get_healthy_session() -> HealthySession:
        return HealthySession()

    app.dependency_overrides[get_async_session] = get_healthy_session
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_health_returns_service_unavailable_when_database_fails() -> None:
    class UnavailableSession:
        async def execute(self, _statement: object) -> None:
            raise OSError("database unavailable")

    async def get_unavailable_session() -> UnavailableSession:
        return UnavailableSession()

    app.dependency_overrides[get_async_session] = get_unavailable_session
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
