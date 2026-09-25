import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app


@pytest.mark.asyncio
async def test_baseline_security_headers_present() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "accelerometer=()" in response.headers.get("permissions-policy", "")
    # Correlation ID must also be present
    assert "x-correlation-id" in response.headers

    # Obsolete / prescriptive headers must NOT be present
    assert "x-xss-protection" not in response.headers
    assert "content-security-policy" not in response.headers


@pytest.mark.asyncio
async def test_authenticated_cache_control_behavior() -> None:
    """Datasets and evaluations endpoints must include Cache-Control: no-store."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Request to dataset endpoint (even unauthorized 401)
        resp_datasets = await client.get("/api/v1/datasets")
        # Request to public health endpoint
        resp_health = await client.get("/api/v1/health")

    assert resp_datasets.headers.get("cache-control") == "no-store"
    # Public health endpoint must not have Cache-Control: no-store forced
    assert resp_health.headers.get("cache-control") != "no-store"


@pytest.mark.asyncio
async def test_hsts_disabled_by_default_and_enabled_via_config() -> None:
    """HSTS should only be emitted when explicitly enabled (e.g. production HTTPS)."""
    transport = ASGITransport(app=app)

    # 1. Default (hsts_enabled = False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res1 = await client.get("/")
    assert "strict-transport-security" not in res1.headers

    # 2. Enabled
    original = settings.hsts_enabled
    try:
        settings.hsts_enabled = True
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            res2 = await client.get("/")
        assert (
            res2.headers.get("strict-transport-security")
            == "max-age=31536000; includeSubDomains"
        )
    finally:
        settings.hsts_enabled = original
