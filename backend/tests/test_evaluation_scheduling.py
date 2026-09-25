"""Comprehensive test suite for Step 15: Evaluation Automation & Scheduling.

Tests:
1. Schedule recurrence algorithms & timezone validation (interval, daily, weekly)
2. Schedule API & CRUD with bounds (min 15m interval, max active per tenant)
3. Pause, resume, and manual trigger endpoints
4. Execution history retrieval and single execution detail
5. Strict tenant isolation (404 on cross-tenant schedule or resources)
6. Concurrency-safe claiming via FOR UPDATE SKIP LOCKED
7. Bounded missed-schedules recovery policy (> 24h window marked missed)
8. Deterministic configuration and dataset snapshotting during schedule execution
9. Production observability & metrics collector tracking
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.v1.evaluation_runs import get_run_executor
from app.auth.dependencies import get_auth_provider
from app.auth.test_provider import TestAuthProvider, make_test_auth_headers
from app.core.config import require_test_database_url, settings
from app.database.session import get_async_session
from app.main import app
from app.models.dataset import Dataset, DatasetCase
from app.models.evaluation_configuration import (
    EvaluationConfig,
    EvaluationConfigVersion,
)
from app.models.evaluation_run import EvaluationRun
from app.models.evaluation_schedule import EvaluationSchedule, ScheduleExecution
from app.models.user import User
from app.observability.events import EvaluationEventType
from app.schemas.evaluation_schedule import (
    EvaluationScheduleCreate,
    ScheduleDefinition,
    ScheduleExecutionStatus,
    ScheduleStatus,
    ScheduleType,
    compute_next_run_at,
)
from app.services import scheduler_service
from app.services.run_executor import SynchronousRunExecutor


@asynccontextmanager
async def managed_schedule_client(
    default_user_id: str = "schedule-test-user-1",
) -> AsyncIterator[AsyncClient]:
    """Test client scoped to TEST database with authenticating principal."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def get_test_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    test_provider = TestAuthProvider(default_user_id=default_user_id)
    app.dependency_overrides[get_async_session] = get_test_db_session
    app.dependency_overrides[get_auth_provider] = lambda: test_provider
    app.dependency_overrides[get_run_executor] = lambda: SynchronousRunExecutor()

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {default_user_id}"},
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(
                text(
                    "TRUNCATE TABLE users, datasets, dataset_cases, "
                    "evaluations, evaluator_configs, evaluation_runs, "
                    "evaluation_results, evaluation_configs, "
                    "evaluation_config_versions, regression_gates, "
                    "regression_gate_versions, regression_gate_evaluations, "
                    "experiments, evaluation_schedules, schedule_executions, "
                    "audit_events CASCADE"
                )
            )
            await cleanup_session.commit()
        await engine.dispose()


async def _seed_test_tenant(
    session: AsyncSession, external_id: str
) -> tuple[User, Dataset, EvaluationConfig]:
    """Seeds a user, dataset with test case, and an evaluation configuration."""
    user = User(
        id=uuid.uuid4(),
        external_auth_id=external_id,
        email=f"{external_id}@example.com",
    )
    session.add(user)
    await session.flush()

    dataset = Dataset(
        id=uuid.uuid4(),
        owner_user_id=user.id,
        name="Scheduled Dataset",
        description="Dataset for schedule testing",
        version=1,
    )
    session.add(dataset)
    await session.flush()

    case = DatasetCase(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        input="What is 2+2?",
        expected_output="4",
    )
    session.add(case)

    evaluators_def: list[dict[str, Any]] = [
        {
            "name": "factuality",
            "evaluator_type": "factuality",
            "backend": "llm_judge",
            "weight": 1.0,
            "threshold": 0.8,
            "enabled": True,
        }
    ]
    config = EvaluationConfig(
        id=uuid.uuid4(),
        owner_user_id=user.id,
        name="Standard Config",
        description="Config for schedules",
        version=1,
        evaluators=evaluators_def,
        snapshot_hash="sched-cfg-hash-v1",
    )
    session.add(config)
    await session.flush()

    config_v1 = EvaluationConfigVersion(
        id=uuid.uuid4(),
        config_id=config.id,
        version=1,
        name=config.name,
        description=config.description,
        evaluators=evaluators_def,
        snapshot_hash="sched-cfg-hash-v1",
    )
    session.add(config_v1)
    await session.commit()
    await session.refresh(user)
    await session.refresh(dataset)
    await session.refresh(config)
    return user, dataset, config


# =============================================================================
# 1. Schedule Recurrence & Timezone Algorithms (compute_next_run_at)
# =============================================================================


def test_compute_next_run_at_interval() -> None:
    """Calculates next run accurately for interval schedules."""
    base_time = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    definition = ScheduleDefinition(
        timezone="UTC",
        interval_minutes=30,
    )

    next_run = compute_next_run_at(
        schedule_type=ScheduleType.INTERVAL,
        definition=definition,
        from_time=base_time,
    )
    assert next_run == datetime(2026, 6, 1, 12, 30, 0, tzinfo=UTC)

    # With end_time boundary
    definition_with_end = ScheduleDefinition(
        timezone="UTC",
        interval_minutes=30,
        end_time=datetime(2026, 6, 1, 12, 15, 0, tzinfo=UTC),
    )
    next_run_expired = compute_next_run_at(
        schedule_type=ScheduleType.INTERVAL,
        definition=definition_with_end,
        from_time=base_time,
    )
    assert next_run_expired is None


def test_compute_next_run_at_daily() -> None:
    """Calculates next run for daily schedule across timezones and day boundaries."""
    # If now is 10:00 UTC and target is 14:00 UTC today -> 14:00 UTC today
    base_time = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    definition = ScheduleDefinition(
        timezone="UTC",
        time_of_day="14:00",
    )
    next_run = compute_next_run_at(
        schedule_type=ScheduleType.DAILY,
        definition=definition,
        from_time=base_time,
    )
    assert next_run == datetime(2026, 6, 1, 14, 0, 0, tzinfo=UTC)

    # If now is 16:00 UTC and target was 14:00 UTC today -> 14:00 UTC tomorrow
    late_time = datetime(2026, 6, 1, 16, 0, 0, tzinfo=UTC)
    next_run_tomorrow = compute_next_run_at(
        schedule_type=ScheduleType.DAILY,
        definition=definition,
        from_time=late_time,
    )
    assert next_run_tomorrow == datetime(2026, 6, 2, 14, 0, 0, tzinfo=UTC)

    # Timezone America/New_York (UTC-4 in daylight saving time June)
    # 09:00 NY time is 13:00 UTC
    ny_definition = ScheduleDefinition(
        timezone="America/New_York",
        time_of_day="09:00",
    )
    next_ny = compute_next_run_at(
        schedule_type=ScheduleType.DAILY,
        definition=ny_definition,
        from_time=base_time,
    )
    assert next_ny == datetime(2026, 6, 1, 13, 0, 0, tzinfo=UTC)


def test_compute_next_run_at_weekly() -> None:
    """Calculates next run for weekly schedule matching specified days of week."""
    # 2026-06-01 is a Monday (weekday 0)
    base_time = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    # Target Tuesday (1) and Thursday (3) at 12:00 UTC
    definition = ScheduleDefinition(
        timezone="UTC",
        time_of_day="12:00",
        days_of_week=[1, 3],
    )
    next_run = compute_next_run_at(
        schedule_type=ScheduleType.WEEKLY,
        definition=definition,
        from_time=base_time,
    )
    # Closest is Tuesday 2026-06-02 12:00 UTC
    assert next_run == datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)

    # After Tuesday run, next is Thursday 2026-06-04 12:00 UTC
    after_tuesday = datetime(2026, 6, 2, 13, 0, 0, tzinfo=UTC)
    next_run_thursday = compute_next_run_at(
        schedule_type=ScheduleType.WEEKLY,
        definition=definition,
        from_time=after_tuesday,
    )
    assert next_run_thursday == datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)


def test_compute_next_run_at_one_time() -> None:
    """Calculates one-time schedule run date, returning None if already in the past."""
    future_time = datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC)
    definition = ScheduleDefinition(
        timezone="UTC",
        start_time=future_time,
    )
    now_before = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    assert (
        compute_next_run_at(ScheduleType.ONE_TIME, definition, from_time=now_before)
        == future_time
    )

    now_after = datetime(2026, 7, 2, 0, 0, 0, tzinfo=UTC)
    assert (
        compute_next_run_at(ScheduleType.ONE_TIME, definition, from_time=now_after)
        is None
    )


def test_schedule_definition_validation_bounds() -> None:
    """Validates boundary rules: min interval >= 15m, valid IANA tz, days 0-6."""
    # 1. Invalid timezone name
    with pytest.raises(ValidationError, match="Invalid IANA timezone name"):
        ScheduleDefinition(timezone="Mars/Olympus_Mons")

    # 2. Interval < 15 minutes
    with pytest.raises(
        ValidationError, match="interval_minutes must be at least 15 minutes"
    ):
        ScheduleDefinition(interval_minutes=10)

    # 3. Invalid days of week (> 6)
    with pytest.raises(
        ValidationError, match="days_of_week items must be 0 \\(Mon\\) to 6 \\(Sun\\)"
    ):
        ScheduleDefinition(days_of_week=[0, 7])

    # 4. Alignment validation in EvaluationScheduleCreate
    dummy_ds_id = uuid.uuid4()
    with pytest.raises(
        ValidationError, match="start_time is required for ONE_TIME schedules"
    ):
        EvaluationScheduleCreate(
            name="Invalid One-Time",
            schedule_type=ScheduleType.ONE_TIME,
            schedule_definition=ScheduleDefinition(),
            dataset_id=dummy_ds_id,
        )

    with pytest.raises(
        ValidationError, match="interval_minutes is required for INTERVAL schedules"
    ):
        EvaluationScheduleCreate(
            name="Invalid Interval",
            schedule_type=ScheduleType.INTERVAL,
            schedule_definition=ScheduleDefinition(),
            dataset_id=dummy_ds_id,
        )

    with pytest.raises(
        ValidationError, match="days_of_week is required for WEEKLY schedules"
    ):
        EvaluationScheduleCreate(
            name="Invalid Weekly",
            schedule_type=ScheduleType.WEEKLY,
            schedule_definition=ScheduleDefinition(),
            dataset_id=dummy_ds_id,
        )


# =============================================================================
# 2. Schedule CRUD & Multi-Tenant Lifecycle API Tests
# =============================================================================


@pytest.mark.asyncio
async def test_schedule_lifecycle_crud() -> None:
    """Full lifecycle: create schedule, inspect, list, update interval, and delete."""
    async with managed_schedule_client(default_user_id="user-crud") as client:
        test_url = require_test_database_url(settings)
        engine = create_async_engine(test_url)
        async_session = async_sessionmaker(engine, expire_on_commit=False)

        async with async_session() as session:
            user, dataset, config = await _seed_test_tenant(session, "user-crud")

        auth_headers = make_test_auth_headers("user-crud")

        # 1. Create interval schedule
        create_payload = {
            "name": "Hourly Quality Evaluation",
            "description": "Evaluates candidate outputs hourly",
            "enabled": True,
            "schedule_type": "interval",
            "schedule_definition": {
                "timezone": "UTC",
                "interval_minutes": 60,
            },
            "dataset_id": str(dataset.id),
            "configuration_id": str(config.id),
        }
        res = await client.post(
            "/api/v1/evaluation-schedules",
            json=create_payload,
            headers=auth_headers,
        )
        assert res.status_code == 201
        sched_data = res.json()
        sched_id = sched_data["id"]
        assert sched_data["name"] == "Hourly Quality Evaluation"
        assert sched_data["enabled"] is True
        assert sched_data["schedule_type"] == "interval"
        assert sched_data["next_run_at"] is not None

        # 2. Get schedule by ID
        res = await client.get(
            f"/api/v1/evaluation-schedules/{sched_id}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["id"] == sched_id

        # 3. List schedules with pagination and filter
        res = await client.get(
            "/api/v1/evaluation-schedules?schedule_type=interval",
            headers=auth_headers,
        )
        assert res.status_code == 200
        list_data = res.json()
        assert list_data["total"] == 1
        assert len(list_data["items"]) == 1
        assert list_data["items"][0]["id"] == sched_id

        # 4. Update schedule definition (change to 120 minutes)
        update_payload = {
            "name": "Bi-Hourly Quality Evaluation",
            "schedule_definition": {
                "timezone": "UTC",
                "interval_minutes": 120,
            },
        }
        res = await client.patch(
            f"/api/v1/evaluation-schedules/{sched_id}",
            json=update_payload,
            headers=auth_headers,
        )
        assert res.status_code == 200
        updated = res.json()
        assert updated["name"] == "Bi-Hourly Quality Evaluation"
        assert updated["schedule_definition"]["interval_minutes"] == 120

        # 5. Delete schedule
        res = await client.delete(
            f"/api/v1/evaluation-schedules/{sched_id}",
            headers=auth_headers,
        )
        assert res.status_code == 204

        # Verify deletion returns 404
        res = await client.get(
            f"/api/v1/evaluation-schedules/{sched_id}",
            headers=auth_headers,
        )
        assert res.status_code == 404
        await engine.dispose()


@pytest.mark.asyncio
async def test_tenant_schedule_limit_enforced() -> None:
    """Enforces maximum active schedules limit per tenant."""
    async with managed_schedule_client(default_user_id="user-limits") as client:
        test_url = require_test_database_url(settings)
        engine = create_async_engine(test_url)
        async_session = async_sessionmaker(engine, expire_on_commit=False)

        async with async_session() as session:
            user, dataset, config = await _seed_test_tenant(session, "user-limits")

            # Seed 20 active schedules directly
            for i in range(settings.max_active_schedules_per_tenant):
                s = EvaluationSchedule(
                    id=uuid.uuid4(),
                    owner_user_id=user.id,
                    name=f"Schedule-{i}",
                    enabled=True,
                    schedule_type=ScheduleType.INTERVAL.value,
                    schedule_definition={"timezone": "UTC", "interval_minutes": 30},
                    dataset_id=dataset.id,
                    configuration_id=config.id,
                    next_run_at=datetime.now(UTC) + timedelta(minutes=30),
                )
                session.add(s)
            await session.commit()

        auth_headers = make_test_auth_headers("user-limits")
        # Attempt to create 21st schedule -> rejected with 400 Bad Request
        res = await client.post(
            "/api/v1/evaluation-schedules",
            json={
                "name": "Excess Schedule",
                "enabled": True,
                "schedule_type": "interval",
                "schedule_definition": {"timezone": "UTC", "interval_minutes": 30},
                "dataset_id": str(dataset.id),
                "configuration_id": str(config.id),
            },
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert "Maximum active schedules limit" in res.json()["detail"]
        await engine.dispose()


@pytest.mark.asyncio
async def test_strict_multi_tenancy_and_cross_tenant_isolation() -> None:
    """Verifies that tenants cannot access or reference others' schedules/resources."""
    async with managed_schedule_client(default_user_id="tenant-a") as client:
        test_url = require_test_database_url(settings)
        engine = create_async_engine(test_url)
        async_session = async_sessionmaker(engine, expire_on_commit=False)

        async with async_session() as session:
            user_a, ds_a, cfg_a = await _seed_test_tenant(session, "tenant-a")
            user_b, ds_b, cfg_b = await _seed_test_tenant(session, "tenant-b")

            # Create schedule owned by Tenant A
            sched_a = EvaluationSchedule(
                id=uuid.uuid4(),
                owner_user_id=user_a.id,
                name="Tenant A Secret Schedule",
                enabled=True,
                schedule_type=ScheduleType.DAILY.value,
                schedule_definition={"timezone": "UTC", "time_of_day": "02:00"},
                dataset_id=ds_a.id,
                configuration_id=cfg_a.id,
                next_run_at=datetime.now(UTC) + timedelta(hours=2),
            )
            session.add(sched_a)
            await session.commit()
            sched_a_id = sched_a.id

        headers_b = make_test_auth_headers("tenant-b")

        # 1. Tenant B cannot read Tenant A's schedule -> 404
        res = await client.get(
            f"/api/v1/evaluation-schedules/{sched_a_id}",
            headers=headers_b,
        )
        assert res.status_code == 404

        # 2. Tenant B cannot update Tenant A's schedule -> 404
        res = await client.patch(
            f"/api/v1/evaluation-schedules/{sched_a_id}",
            json={"name": "Hijacked Schedule"},
            headers=headers_b,
        )
        assert res.status_code == 404

        # 3. Tenant B cannot pause or resume Tenant A's schedule -> 404
        res = await client.post(
            f"/api/v1/evaluation-schedules/{sched_a_id}/pause",
            headers=headers_b,
        )
        assert res.status_code == 404

        res = await client.post(
            f"/api/v1/evaluation-schedules/{sched_a_id}/resume",
            headers=headers_b,
        )
        assert res.status_code == 404

        # 4. Tenant B cannot trigger Tenant A's schedule -> 404
        res = await client.post(
            f"/api/v1/evaluation-schedules/{sched_a_id}/trigger",
            headers=headers_b,
        )
        assert res.status_code == 404

        # 5. Tenant B cannot delete Tenant A's schedule -> 404
        res = await client.delete(
            f"/api/v1/evaluation-schedules/{sched_a_id}",
            headers=headers_b,
        )
        assert res.status_code == 404

        # 6. Tenant B cannot create a schedule referencing Tenant A's dataset -> 404
        res = await client.post(
            "/api/v1/evaluation-schedules",
            json={
                "name": "Cross Dataset Schedule",
                "schedule_type": "daily",
                "schedule_definition": {"timezone": "UTC", "time_of_day": "04:00"},
                "dataset_id": str(ds_a.id),  # Belongs to Tenant A!
                "configuration_id": str(cfg_b.id),
            },
            headers=headers_b,
        )
        assert res.status_code == 404
        assert "Dataset not found" in res.json()["detail"]

        # 7. Tenant B cannot create schedule with Tenant A's config -> 404
        res = await client.post(
            "/api/v1/evaluation-schedules",
            json={
                "name": "Cross Config Schedule",
                "schedule_type": "daily",
                "schedule_definition": {"timezone": "UTC", "time_of_day": "04:00"},
                "dataset_id": str(ds_b.id),
                "configuration_id": str(cfg_a.id),  # Belongs to Tenant A!
            },
            headers=headers_b,
        )
        assert res.status_code == 404
        assert "Evaluation configuration not found" in res.json()["detail"]

        await engine.dispose()


# =============================================================================
# 3. Pause, Resume, Manual Trigger & Execution History Tests
# =============================================================================


@pytest.mark.asyncio
async def test_pause_resume_and_manual_trigger() -> None:
    """Tests pausing, resuming, triggering manually, and reading execution history."""
    async with managed_schedule_client(default_user_id="user-controls") as client:
        test_url = require_test_database_url(settings)
        engine = create_async_engine(test_url)
        async_session = async_sessionmaker(engine, expire_on_commit=False)

        async with async_session() as session:
            user, dataset, config = await _seed_test_tenant(session, "user-controls")

        auth_headers = make_test_auth_headers("user-controls")

        # 1. Create schedule
        res = await client.post(
            "/api/v1/evaluation-schedules",
            json={
                "name": "Daily Quality Run",
                "enabled": True,
                "schedule_type": "daily",
                "schedule_definition": {"timezone": "UTC", "time_of_day": "03:00"},
                "dataset_id": str(dataset.id),
                "configuration_id": str(config.id),
            },
            headers=auth_headers,
        )
        assert res.status_code == 201
        sched_id = res.json()["id"]

        # 2. Pause schedule
        res = await client.post(
            f"/api/v1/evaluation-schedules/{sched_id}/pause",
            headers=auth_headers,
        )
        assert res.status_code == 200
        paused = res.json()
        assert paused["enabled"] is False
        assert paused["next_run_at"] is None
        assert paused["last_status"] == ScheduleStatus.PAUSED.value

        # 3. Resume schedule
        res = await client.post(
            f"/api/v1/evaluation-schedules/{sched_id}/resume",
            headers=auth_headers,
        )
        assert res.status_code == 200
        resumed = res.json()
        assert resumed["enabled"] is True
        assert resumed["next_run_at"] is not None

        # 4. Trigger schedule manually
        res = await client.post(
            f"/api/v1/evaluation-schedules/{sched_id}/trigger",
            headers=auth_headers,
        )
        assert res.status_code == 202
        exec_record = res.json()
        exec_id = exec_record["id"]
        assert (
            exec_record["execution_status"] == ScheduleExecutionStatus.SUCCEEDED.value
        )
        assert exec_record["run_id"] is not None

        # 5. List executions history
        res = await client.get(
            f"/api/v1/evaluation-schedules/{sched_id}/executions",
            headers=auth_headers,
        )
        assert res.status_code == 200
        history = res.json()
        assert history["total"] == 1
        assert history["items"][0]["id"] == exec_id

        # 6. Get single execution detail
        res = await client.get(
            f"/api/v1/evaluation-schedules/{sched_id}/executions/{exec_id}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["id"] == exec_id
        assert res.json()["execution_status"] == ScheduleExecutionStatus.SUCCEEDED.value

        # 7. Check updated schedule state
        res = await client.get(
            f"/api/v1/evaluation-schedules/{sched_id}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        sched_after = res.json()
        assert sched_after["last_run_id"] == exec_record["run_id"]
        assert sched_after["last_status"] == ScheduleStatus.SUCCEEDED.value

        await engine.dispose()


# =============================================================================
# 4. Concurrency & Transactional Claiming (FOR UPDATE SKIP LOCKED)
# =============================================================================


@pytest.mark.asyncio
async def test_concurrency_claiming_skip_locked() -> None:
    """Verifies that SKIP LOCKED prevents duplicate concurrent worker claims."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        user, dataset, config = await _seed_test_tenant(session, "user-concurrency")

        # Create two due schedules (next_run_at in the past)
        sched1 = EvaluationSchedule(
            id=uuid.uuid4(),
            owner_user_id=user.id,
            name="Due Sched 1",
            enabled=True,
            schedule_type=ScheduleType.INTERVAL.value,
            schedule_definition={"timezone": "UTC", "interval_minutes": 15},
            dataset_id=dataset.id,
            configuration_id=config.id,
            next_run_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        sched2 = EvaluationSchedule(
            id=uuid.uuid4(),
            owner_user_id=user.id,
            name="Due Sched 2",
            enabled=True,
            schedule_type=ScheduleType.INTERVAL.value,
            schedule_definition={"timezone": "UTC", "interval_minutes": 30},
            dataset_id=dataset.id,
            configuration_id=config.id,
            next_run_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        session.add_all([sched1, sched2])
        await session.commit()

    # Worker 1 starts transaction and locks due schedules
    session1 = async_session()
    session2 = async_session()

    try:
        now = datetime.now(UTC)
        claim_stmt = (
            select(EvaluationSchedule)
            .where(
                EvaluationSchedule.enabled.is_(True),
                EvaluationSchedule.next_run_at.is_not(None),
                EvaluationSchedule.next_run_at <= now,
            )
            .with_for_update(skip_locked=True)
        )

        # Worker 1 claims both rows
        claimed_w1 = (await session1.scalars(claim_stmt)).all()
        assert len(claimed_w1) == 2

        # Worker 2 simultaneously queries for due schedules
        # Because Worker 1 has locked them with SKIP LOCKED, Worker 2 must find 0 rows
        claimed_w2 = (await session2.scalars(claim_stmt)).all()
        assert len(claimed_w2) == 0

        # Rollback Worker 1
        await session1.rollback()
    finally:
        await session1.close()
        await session2.close()
        async with async_session() as cleanup_session:
            await cleanup_session.execute(
                text(
                    "TRUNCATE TABLE users, datasets, dataset_cases, "
                    "evaluations, evaluator_configs, evaluation_runs, "
                    "evaluation_results, evaluation_configs, "
                    "evaluation_config_versions, regression_gates, "
                    "regression_gate_versions, regression_gate_evaluations, "
                    "experiments, evaluation_schedules, schedule_executions CASCADE"
                )
            )
            await cleanup_session.commit()
        await engine.dispose()


# =============================================================================
# 5. Bounded Missed-Schedules Catch-Up Policy Tests
# =============================================================================


@pytest.mark.asyncio
async def test_bounded_missed_schedule_recovery() -> None:
    """Verifies that schedules overdue by > 24h are marked MISSED without backfill."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        user, dataset, config = await _seed_test_tenant(session, "user-catchup")

        # 1. Schedule severely overdue (30 hours ago, > 24h limit)
        sched_overdue = EvaluationSchedule(
            id=uuid.uuid4(),
            owner_user_id=user.id,
            name="Severely Overdue Schedule",
            enabled=True,
            schedule_type=ScheduleType.DAILY.value,
            schedule_definition={"timezone": "UTC", "time_of_day": "01:00"},
            dataset_id=dataset.id,
            configuration_id=config.id,
            next_run_at=datetime.now(UTC) - timedelta(hours=30),
        )

        # 2. Schedule reasonably overdue (1 hour ago, < 24h limit)
        sched_normal = EvaluationSchedule(
            id=uuid.uuid4(),
            owner_user_id=user.id,
            name="Recent Due Schedule",
            enabled=True,
            schedule_type=ScheduleType.INTERVAL.value,
            schedule_definition={"timezone": "UTC", "interval_minutes": 60},
            dataset_id=dataset.id,
            configuration_id=config.id,
            next_run_at=datetime.now(UTC) - timedelta(hours=1),
        )
        session.add_all([sched_overdue, sched_normal])
        await session.commit()
        sched_overdue_id = sched_overdue.id
        sched_normal_id = sched_normal.id

    # Run scheduler processor with SynchronousRunExecutor
    async with async_session() as run_session:
        executor = SynchronousRunExecutor()
        executed_count = await scheduler_service.poll_and_execute_due_schedules(
            session=run_session,
            run_executor=executor,
            batch_size=10,
        )
        # Exactly 1 was executed as a run (the normal due one)
        assert executed_count == 1

    # Verify database state
    async with async_session() as verify_session:
        so = await verify_session.get(EvaluationSchedule, sched_overdue_id)
        assert so is not None
        assert so.last_status == ScheduleStatus.MISSED.value
        # Next run is advanced to future window
        assert so.next_run_at is not None
        assert so.next_run_at > datetime.now(UTC)

        # Verify execution record for overdue schedule is marked MISSED
        stmt_so = select(ScheduleExecution).where(
            ScheduleExecution.schedule_id == sched_overdue_id
        )
        exec_so = (await verify_session.scalars(stmt_so)).first()
        assert exec_so is not None
        assert exec_so.execution_status == ScheduleExecutionStatus.MISSED.value
        assert exec_so.error_code == "SCHEDULE_WINDOW_EXPIRED"

        # Verify execution record for normal due schedule is SUCCEEDED
        sn = await verify_session.get(EvaluationSchedule, sched_normal_id)
        assert sn is not None
        assert sn.last_status == ScheduleStatus.SUCCEEDED.value
        stmt_sn = select(ScheduleExecution).where(
            ScheduleExecution.schedule_id == sched_normal_id
        )
        exec_sn = (await verify_session.scalars(stmt_sn)).first()
        assert exec_sn is not None
        assert exec_sn.execution_status == ScheduleExecutionStatus.SUCCEEDED.value
        assert exec_sn.run_id is not None

        # Clean up
        await verify_session.execute(
            text(
                "TRUNCATE TABLE users, datasets, dataset_cases, "
                "evaluations, evaluator_configs, evaluation_runs, "
                "evaluation_results, evaluation_configs, "
                "evaluation_config_versions, regression_gates, "
                "regression_gate_versions, regression_gate_evaluations, "
                "experiments, evaluation_schedules, schedule_executions CASCADE"
            )
        )
        await verify_session.commit()

    await engine.dispose()


# =============================================================================
# 6. Deterministic Configuration & Dataset Snapshotting Tests
# =============================================================================


@pytest.mark.asyncio
async def test_schedule_execution_pinned_vs_unpinned_configuration() -> None:
    """Verifies that runs deterministically respect pinned config versions."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        user, dataset, config = await _seed_test_tenant(session, "user-snapshot")

        # Mutate configuration to version 2
        evals_v2: list[dict[str, Any]] = [
            {
                "name": "factuality",
                "evaluator_type": "factuality",
                "backend": "llm_judge",
                "weight": 2.0,
                "threshold": 0.9,
                "enabled": True,
            }
        ]
        config.version = 2
        config.evaluators = evals_v2
        config.snapshot_hash = "sched-cfg-hash-v2"

        v2_record = EvaluationConfigVersion(
            id=uuid.uuid4(),
            config_id=config.id,
            version=2,
            name=config.name,
            evaluators=evals_v2,
            snapshot_hash="sched-cfg-hash-v2",
        )
        session.add(v2_record)

        # Create schedule explicitly pinned to version 1
        pinned_sched = EvaluationSchedule(
            id=uuid.uuid4(),
            owner_user_id=user.id,
            name="Pinned V1 Schedule",
            enabled=True,
            schedule_type=ScheduleType.INTERVAL.value,
            schedule_definition={"timezone": "UTC", "interval_minutes": 30},
            dataset_id=dataset.id,
            configuration_id=config.id,
            configuration_version=1,  # Pinned to v1!
            next_run_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        session.add(pinned_sched)
        await session.commit()
        pinned_id = pinned_sched.id

    # Execute schedule
    async with async_session() as run_session:
        executor = SynchronousRunExecutor()
        sched = await run_session.get(EvaluationSchedule, pinned_id)
        assert sched is not None
        execution = await scheduler_service._execute_single_schedule(
            session=run_session,
            schedule=sched,
            run_executor=executor,
        )
        assert execution.execution_status == ScheduleExecutionStatus.SUCCEEDED.value
        assert execution.run_id is not None
        run_id = execution.run_id

    # Verify the created EvaluationRun used version 1
    async with async_session() as verify_session:
        run = await verify_session.get(EvaluationRun, run_id)
        assert run is not None
        assert run.config_version == 1
        assert run.config_snapshot_hash == "sched-cfg-hash-v1"

        # Cleanup
        await verify_session.execute(
            text(
                "TRUNCATE TABLE users, datasets, dataset_cases, "
                "evaluations, evaluator_configs, evaluation_runs, "
                "evaluation_results, evaluation_configs, "
                "evaluation_config_versions, regression_gates, "
                "regression_gate_versions, regression_gate_evaluations, "
                "experiments, evaluation_schedules, schedule_executions CASCADE"
            )
        )
        await verify_session.commit()

    await engine.dispose()


# =============================================================================
# 7. Production Observability & Audit Integration Tests
# =============================================================================


@pytest.mark.asyncio
async def test_schedule_lifecycle_observability_and_audit() -> None:
    """Verifies that schedule actions record structured audit events and metrics."""
    async with managed_schedule_client(default_user_id="user-observability") as client:
        test_url = require_test_database_url(settings)
        engine = create_async_engine(test_url)
        async_session = async_sessionmaker(engine, expire_on_commit=False)

        async with async_session() as session:
            user, dataset, config = await _seed_test_tenant(
                session, "user-observability"
            )

        auth_headers = make_test_auth_headers("user-observability")

        # 1. Create schedule -> emits SCHEDULE_CREATED
        res = await client.post(
            "/api/v1/evaluation-schedules",
            json={
                "name": "Audit Tracked Schedule",
                "enabled": True,
                "schedule_type": "interval",
                "schedule_definition": {"timezone": "UTC", "interval_minutes": 30},
                "dataset_id": str(dataset.id),
                "configuration_id": str(config.id),
            },
            headers=auth_headers,
        )
        assert res.status_code == 201
        sched_id = res.json()["id"]

        # 2. Trigger manually -> emits SCHEDULE_TRIGGERED, STARTED, COMPLETED
        res = await client.post(
            f"/api/v1/evaluation-schedules/{sched_id}/trigger",
            headers=auth_headers,
        )
        assert res.status_code == 202

        # 3. Check metrics collector endpoint
        metrics_res = await client.get(
            "/api/v1/metrics",
            headers=auth_headers,
        )
        assert metrics_res.status_code == 200
        metrics_data = metrics_res.json()
        assert "schedule" in metrics_data
        sched_metrics = metrics_data["schedule"]
        assert sched_metrics["schedules_created_total"] >= 1
        assert sched_metrics["schedules_triggered_total"] >= 1
        assert sched_metrics["executions_completed_total"] >= 1

        # 4. Check audit events endpoint
        events_res = await client.get(
            "/api/v1/audit-events?resource_type=schedule",
            headers=auth_headers,
        )
        assert events_res.status_code == 200
        events_data = events_res.json()
        event_types = [e["event_type"] for e in events_data["items"]]
        assert EvaluationEventType.SCHEDULE_CREATED.value in event_types
        assert EvaluationEventType.SCHEDULE_TRIGGERED.value in event_types
        assert EvaluationEventType.SCHEDULE_EXECUTION_STARTED.value in event_types
        assert EvaluationEventType.SCHEDULE_EXECUTION_COMPLETED.value in event_types

        await engine.dispose()
