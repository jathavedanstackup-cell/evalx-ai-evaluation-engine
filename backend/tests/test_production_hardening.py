"""Comprehensive tests for Step 16: Production Deployment & Reliability Hardening.

Covers:
1. Production config validation (missing secrets, insecure passwords, hosts)
2. Safe secret masking & sanitization
3. Health probe reliability (independent liveness, timeout-protected readiness)
4. Database pool configuration and connection reliability
5. Redis failure & outage handling in readiness
6. Worker resilience: stale run reconciliation & orphaned execution cleanup
7. CORS origin safety and credential boundaries
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import (
    Settings,
    check_production_settings,
    get_sanitized_settings,
    require_test_database_url,
    settings,
    validate_production_settings,
)
from app.database.session import (
    check_database_connectivity,
    get_async_engine,
    get_async_session,
)
from app.evaluation.lifecycle import RunStatus
from app.main import app
from app.models.dataset import Dataset
from app.models.evaluation import Evaluation
from app.models.evaluation_run import EvaluationRun
from app.models.user import User
from app.worker.health import check_worker_health
from app.worker.reconciliation import reconcile_stale_runs

# =============================================================================
# 1. Configuration Validation & Secret Sanitization Tests
# =============================================================================


def test_production_settings_validation_missing_keys() -> None:
    """Production mode must reject missing DATABASE_URL and REDIS_URL."""
    bad_cfg = Settings(
        environment="production",
        database_url=None,
        redis_url="",
        clerk_secret_key=None,
        clerk_jwt_key=None,
        cors_allowed_origins="https://app.evalx.ai",
    )
    errors = check_production_settings(bad_cfg)
    assert any("DATABASE_URL is required" in e for e in errors)
    assert any("REDIS_URL is required" in e for e in errors)
    assert any("Production authentication requires" in e for e in errors)

    with pytest.raises(ValueError, match="Production configuration validation failed"):
        validate_production_settings(bad_cfg)


def test_production_settings_validation_insecure_defaults() -> None:
    """Production mode must reject dev default passwords and localhost bindings."""
    insecure_cfg = Settings(
        environment="production",
        database_url="postgresql+psycopg://evalx:evalx_dev_password@db.prod.internal:5432/evalx",
        redis_url="redis://localhost:6379/0",
        clerk_secret_key="sk_live_validsecretkey12345",
        cors_allowed_origins="http://localhost:3000",
    )
    errors = check_production_settings(insecure_cfg)
    assert any("Insecure default password" in e for e in errors)
    assert any("Localhost REDIS_URL host is not permitted" in e for e in errors)
    assert any("Localhost CORS origin" in e for e in errors)


def test_production_settings_validation_wildcard_cors() -> None:
    """Production mode strictly forbids wildcard '*' CORS origin."""
    wildcard_cfg = Settings(
        environment="production",
        database_url="postgresql+psycopg://evalx:strongprodpass123@db.prod.internal:5432/evalx",
        redis_url="redis://redis.prod.internal:6379/0",
        clerk_secret_key="sk_live_validsecretkey12345",
        cors_allowed_origins="*",
    )
    errors = check_production_settings(wildcard_cfg)
    assert any("Wildcard CORS origin '*' is strictly forbidden" in e for e in errors)


def test_production_settings_validation_valid() -> None:
    """Valid production settings pass validation without raising errors."""
    valid_cfg = Settings(
        environment="production",
        database_url="postgresql+psycopg://evalx_app:P@ssw0rd999!@db.internal:5432/evalx_prod",
        redis_url="redis://cache.internal:6379/0",
        clerk_secret_key="sk_live_999999999999999999",
        cors_allowed_origins="https://app.evalx.ai,https://admin.evalx.ai",
        security_headers_enabled=True,
    )
    errors = check_production_settings(valid_cfg)
    assert errors == []
    # Must not raise
    validate_production_settings(valid_cfg)


def test_sanitized_settings_no_secret_leakage() -> None:
    """Confirms get_sanitized_settings masks passwords and keys with '***'."""
    test_cfg = Settings(
        database_url="postgresql+psycopg://evalx:supersecretpw123@db.example.com:5432/evalx",
        redis_url="redis://:myredispassword@redis.example.com:6379/0",
        clerk_secret_key="sk_live_veryconfidentialsecret",
        clerk_jwt_key="-----BEGIN PUBLIC KEY-----\nSecretKey\n-----END PUBLIC KEY-----",
    )
    sanitized = get_sanitized_settings(test_cfg)
    assert "supersecretpw123" not in str(sanitized)
    assert "myredispassword" not in str(sanitized)
    assert "veryconfidentialsecret" not in str(sanitized)
    assert (
        sanitized["database_url"]
        == "postgresql+psycopg://evalx:***@db.example.com:5432/evalx"
    )
    assert sanitized["redis_url"] == "redis://:***@redis.example.com:6379/0"
    assert sanitized["clerk_secret_key"] == "***"
    assert sanitized["clerk_jwt_key"] == "***"


# =============================================================================
# 2. Database Connection Pool & Connectivity Probes
# =============================================================================


def test_database_pool_configuration_parameters() -> None:
    """Verify database pool settings are configured with safe production defaults."""
    assert settings.db_pool_size >= 5
    assert settings.db_max_overflow >= 5
    assert settings.db_pool_timeout_seconds >= 10.0
    assert settings.db_pool_recycle_seconds >= 300

    # Ensure engine creation respects settings
    engine = get_async_engine()
    assert engine.pool is not None


@pytest.mark.asyncio
async def test_database_connectivity_helper() -> None:
    """Verify check_database_connectivity probe helper succeeds against active DB."""
    ok = await check_database_connectivity(timeout_seconds=3.0)
    assert ok is True


# =============================================================================
# 3. Health Probes & Dependency Outage Handling
# =============================================================================


@pytest.mark.asyncio
async def test_health_live_probe_independence() -> None:
    """Liveness probe must return 200 OK without depending on DB or Redis."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/api/v1/health/live")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_ready_probe_database_failure() -> None:
    """Readiness probe must return 503 Service Unavailable when DB is failing."""

    async def failing_session() -> AsyncIterator[AsyncSession]:
        mock_sess = AsyncMock(spec=AsyncSession)
        mock_sess.execute.side_effect = OSError("Connection refused by database")
        yield mock_sess

    app.dependency_overrides[get_async_session] = failing_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            res = await client.get("/api/v1/health/ready")
            assert res.status_code == 503
            assert res.json()["detail"] == "Database unavailable"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_ready_probe_redis_failure() -> None:
    """Readiness probe must return 503 Service Unavailable when Redis is unreachable."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def get_test_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_async_session] = get_test_db
    transport = ASGITransport(app=app)
    try:
        with patch("app.api.v1.health.Redis.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.ping.side_effect = ConnectionError("Redis connection refused")
            mock_from_url.return_value = mock_client

            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                res = await client.get("/api/v1/health/ready")
                assert res.status_code == 503
                assert res.json()["detail"] == "Redis unavailable"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_health_check_reporting() -> None:
    """Tests worker health check reports degraded/unhealthy on missing heartbeat."""
    mock_redis = AsyncMock()
    mock_redis.ping.return_value = True
    # No heartbeat found in Redis
    mock_redis.get.return_value = None

    health = await check_worker_health(redis_client=mock_redis)
    assert health["redis_reachable"] is True
    assert health["worker_heartbeat"] == "stale"
    assert health["status"] == "degraded"


# =============================================================================
# 4. Worker & Run Resilience: Stale Run Reconciliation
# =============================================================================


@pytest.mark.asyncio
async def test_stale_run_reconciliation() -> None:
    """Reconciles orphaned runs stuck in RUNNING beyond cutoff to FAILED."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        # Seed user & dataset & evaluation
        user = User(
            id=uuid.uuid4(),
            external_auth_id="user-stale-reconcile",
            email="stale@example.com",
        )
        session.add(user)
        await session.flush()

        ds = Dataset(
            id=uuid.uuid4(),
            owner_user_id=user.id,
            name="Stale Test Dataset",
            version=1,
        )
        session.add(ds)
        await session.flush()

        eval_ent = Evaluation(
            id=uuid.uuid4(),
            dataset_id=ds.id,
            name="Stale Evaluation Test",
            model_provider="openai",
            model_name="gpt-4o-mini",
        )
        session.add(eval_ent)
        await session.flush()

        # Create an orphaned run that started 4 hours ago and got stuck
        four_hours_ago = datetime.now(UTC) - timedelta(hours=4)
        orphaned_run = EvaluationRun(
            id=uuid.uuid4(),
            evaluation_id=eval_ent.id,
            status=RunStatus.RUNNING.value,
            started_at=four_hours_ago,
        )
        session.add(orphaned_run)

        # Create a fresh run that started 10 minutes ago
        ten_mins_ago = datetime.now(UTC) - timedelta(minutes=10)
        fresh_run = EvaluationRun(
            id=uuid.uuid4(),
            evaluation_id=eval_ent.id,
            status=RunStatus.RUNNING.value,
            started_at=ten_mins_ago,
        )
        session.add(fresh_run)
        await session.commit()

        # Run reconciliation with 2-hour timeout
        reconciled = await reconcile_stale_runs(session, stale_timeout_hours=2)
        assert reconciled == 1

        # Verify orphaned run is now FAILED
        refreshed_orphaned = await session.get(EvaluationRun, orphaned_run.id)
        assert refreshed_orphaned is not None
        assert refreshed_orphaned.status == RunStatus.FAILED.value
        assert "Execution timed out or worker stalled" in str(
            refreshed_orphaned.error_message
        )

        # Verify fresh run is still RUNNING
        refreshed_fresh = await session.get(EvaluationRun, fresh_run.id)
        assert refreshed_fresh is not None
        assert refreshed_fresh.status == RunStatus.RUNNING.value

        # Cleanup
        await session.execute(
            text(
                "TRUNCATE TABLE users, datasets, dataset_cases, evaluations, "
                "evaluation_runs, evaluation_results, audit_events CASCADE"
            )
        )
        await session.commit()

    await engine.dispose()
