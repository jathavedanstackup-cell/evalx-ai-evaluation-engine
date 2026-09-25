import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

from app.auth.dependencies import get_auth_provider
from app.auth.test_provider import TestAuthProvider, make_test_auth_headers
from app.database.session import get_async_session
from app.main import app
from app.security.rate_limiter import (
    InMemoryRateLimiter,
    RateLimitTier,
    get_rate_limiter,
)
from tests.test_datasets_api import managed_api_client


@pytest.mark.asyncio
async def test_sql_error_response_sanitization() -> None:
    """Verifies DB errors return 500 and never leak SQL, params, or schema."""

    async def get_failing_session() -> None:
        # Simulate an operational database crash with a sensitive query
        stmt = "SELECT secret_key, user_password FROM users WHERE email='victim@co.com'"
        raise OperationalError(
            statement=stmt,
            params={"param_secret": "my-secret-key-123"},
            orig=Exception("FATAL: connection terminated unexpectedly"),
        )

    app.dependency_overrides[get_auth_provider] = lambda: TestAuthProvider(
        default_user_id="test-user"
    )
    app.dependency_overrides[get_async_session] = get_failing_session
    transport = ASGITransport(app=app)
    auth_headers = make_test_auth_headers(user_id="test-user")

    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            headers = {**auth_headers, "X-Correlation-ID": "test-cid-sql-error"}
            response = await client.get("/api/v1/datasets", headers=headers)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    data = response.json()

    # Generic sanitized client message
    assert data["error_code"] == "DATABASE_ERROR"
    assert data["detail"] == "An internal database error occurred."
    assert data["correlation_id"] == "test-cid-sql-error"
    assert response.headers.get("x-correlation-id") == "test-cid-sql-error"

    # Strict check: NEVER expose SQL or credentials in client response
    body_text = response.text
    assert "SELECT" not in body_text
    assert "user_password" not in body_text
    assert "my-secret-key-123" not in body_text
    assert "victim@company.com" not in body_text
    assert "victim@co.com" not in body_text


@pytest.mark.asyncio
async def test_unhandled_exception_traceback_suppression() -> None:
    """Verifies uncaught exceptions return 500 without stack traces or paths."""

    async def crashing_session() -> None:
        raise ZeroDivisionError("division by zero in /internal/evalx/secrets.py")

    app.dependency_overrides[get_auth_provider] = lambda: TestAuthProvider(
        default_user_id="test-user"
    )
    app.dependency_overrides[get_async_session] = crashing_session
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    auth_headers = make_test_auth_headers(user_id="test-user")

    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            headers = {**auth_headers, "X-Correlation-ID": "test-cid-unhandled"}
            response = await client.get("/api/v1/datasets", headers=headers)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    data = response.json()
    assert data["error_code"] == "INTERNAL_SERVER_ERROR"
    assert data["detail"] == "An unexpected internal error occurred."
    assert data["correlation_id"] == "test-cid-unhandled"

    # Never leak Python tracebacks or file paths
    body_text = response.text
    assert "Traceback" not in body_text
    assert "ZeroDivisionError" not in body_text
    assert "/internal/evalx" not in body_text


@pytest.mark.asyncio
async def test_correlation_id_preserved_across_all_error_statuses() -> None:
    """Verifies X-Correlation-ID is preserved across all error response statuses."""
    auth_headers = make_test_auth_headers("cid-user")
    custom_cid = "evalx-test-correlation-id-preserve"
    base_headers = {"X-Correlation-ID": custom_cid}

    async with managed_api_client() as client:
        # 1. 401 Unauthorized (missing token when default user cleared)
        app.dependency_overrides[get_auth_provider] = lambda: TestAuthProvider(
            default_user_id=None
        )
        res_401 = await client.get("/api/v1/datasets", headers=base_headers)
        assert res_401.status_code == 401
        assert res_401.headers.get("x-correlation-id") == custom_cid

        # Reset auth provider
        app.dependency_overrides[get_auth_provider] = lambda: TestAuthProvider(
            default_user_id="cid-user"
        )

        # 2. 404 Not Found
        res_404 = await client.get(
            f"/api/v1/datasets/{uuid.uuid4()}",
            headers={**auth_headers, **base_headers},
        )
        assert res_404.status_code == 404
        assert res_404.headers.get("x-correlation-id") == custom_cid

        # 3. 422 Validation Error
        res_422 = await client.post(
            "/api/v1/datasets",
            json={"name": "   "},
            headers={**auth_headers, **base_headers},
        )
        assert res_422.status_code == 422
        assert res_422.headers.get("x-correlation-id") == custom_cid


@pytest.mark.asyncio
async def test_rate_limit_exceeded_error_envelope_and_headers() -> None:
    """Verifies 429 response structure, Retry-After, and headers."""
    limiter = InMemoryRateLimiter()
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    custom_cid = "evalx-rate-limit-test-cid"

    try:
        async with managed_api_client() as client:
            # Create a dataset to provision user and get owner UUID
            ds_res = await client.post("/api/v1/datasets", json={"name": "RL Dataset"})
            assert ds_res.status_code == 201
            ds_data = ds_res.json()
            ds_id = ds_data["id"]
            user_id = ds_data["owner_user_id"]

            key = f"evalx:rl:runs:{user_id}"

            # Pre-fill limiter storage for user to exhaust 10 runs
            for _ in range(10):
                await limiter.acquire(key, limit=10, tier=RateLimitTier.RUNS)

            # Next run launch must trigger 429
            payload = {
                "dataset_id": ds_id,
                "evaluators": [{"evaluator_type": "factuality", "backend": "native"}],
            }
            res_429 = await client.post(
                "/api/v1/evaluations/runs",
                json=payload,
                headers={"X-Correlation-ID": custom_cid},
            )

        assert res_429.status_code == 429
        assert "Retry-After" in res_429.headers
        assert res_429.headers.get("x-correlation-id") == custom_cid
        data = res_429.json()
        assert data["error_code"] == "RATE_LIMIT_EXCEEDED"
        assert data["correlation_id"] == custom_cid
        assert "Too many requests" in data["detail"]
    finally:
        app.dependency_overrides.clear()
