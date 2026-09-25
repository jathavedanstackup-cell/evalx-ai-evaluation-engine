"""Comprehensive test suite for Step 14: Production Observability & Auditability.

Tests structured events, durable audit persistence, immutability & SET NULL semantics,
tenant isolation, chronological run audit trails, in-process metrics collector,
sensitive data redaction filter, and retention pruning.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.v1.evaluation_runs import get_run_executor
from app.auth.dependencies import get_auth_provider
from app.auth.test_provider import TestAuthProvider
from app.core.config import require_test_database_url, settings
from app.database.session import get_async_session
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.dataset import Dataset
from app.models.evaluation import Evaluation
from app.models.evaluation_run import EvaluationRun
from app.models.user import User
from app.observability.correlation import (
    CORRELATION_ID_MAX_LENGTH,
    validate_and_normalize_correlation_id,
)
from app.observability.events import EvaluationEvent, EvaluationEventType
from app.observability.logging import (
    SensitiveDataFilter,
    redact_sensitive_string,
)
from app.observability.metrics import MetricsCollector
from app.observability.sanitization import (
    sanitize_for_observability,
)
from app.services import audit_service
from app.services.run_executor import SynchronousRunExecutor


@asynccontextmanager
async def managed_audit_client(
    user_id: str = "audit-test-user-1",
) -> AsyncIterator[AsyncClient]:
    """Test client scoped to TEST database with authenticating principal."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def get_test_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = get_test_db_session
    app.dependency_overrides[get_auth_provider] = lambda: TestAuthProvider(
        default_user_id=user_id,
    )
    app.dependency_overrides[get_run_executor] = lambda: SynchronousRunExecutor()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {user_id}"},
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


# =============================================================================
# 1. Event Taxonomy & Structure Tests
# =============================================================================


def test_event_taxonomy_completeness() -> None:
    """Verifies that all required event types in the taxonomy are explicitly defined."""
    required_events = [
        # Request
        "REQUEST_STARTED",
        "REQUEST_COMPLETED",
        "REQUEST_FAILED",
        # Run
        "RUN_CREATED",
        "RUN_STARTED",
        "RUN_COMPLETED",
        "RUN_FAILED",
        "RUN_CANCELLED",
        "RUN_TIMED_OUT",
        # Case
        "CASE_STARTED",
        "CASE_COMPLETED",
        "CASE_FAILED",
        # Evaluator
        "EVALUATOR_STARTED",
        "EVALUATOR_COMPLETED",
        "EVALUATOR_FAILED",
        "EVALUATOR_UNAVAILABLE",
        # Config
        "CONFIG_CREATED",
        "CONFIG_UPDATED",
        "CONFIG_DELETED",
        # Gate
        "GATE_CREATED",
        "GATE_UPDATED",
        "GATE_DELETED",
        "GATE_EVALUATED",
        # Analysis
        "ANALYSIS_STARTED",
        "ANALYSIS_COMPLETED",
        "ANALYSIS_FAILED",
        # Queue / Worker
        "QUEUE_SUBMITTED",
        "QUEUE_FAILED",
        "WORKER_STARTED",
        "WORKER_COMPLETED",
        "WORKER_FAILED",
    ]

    for ev_name in required_events:
        assert hasattr(EvaluationEventType, ev_name), (
            f"Missing {ev_name} in EvaluationEventType"
        )
        ev_val = getattr(EvaluationEventType, ev_name)
        assert isinstance(ev_val.value, str)
        assert len(ev_val.value) > 0


def test_evaluation_event_immutability_and_serialization() -> None:
    """Verifies that EvaluationEvent is frozen and captures audit context."""
    ev = EvaluationEvent(
        event_type=EvaluationEventType.RUN_CREATED,
        correlation_id="evalx-test-cid",
        run_id=uuid.uuid4(),
        status="pending",
        details={"cases": 5},
    )
    assert ev.event_type == EvaluationEventType.RUN_CREATED
    assert ev.correlation_id == "evalx-test-cid"

    with pytest.raises(ValidationError):
        # Model is frozen
        ev.status = "running"  # type: ignore[misc]


# =============================================================================
# 2. Correlation ID Safety & Propagation Tests
# =============================================================================


def test_correlation_id_validation_and_sanitization() -> None:
    """Verifies correlation ID normalization, CRLF rejection, and length bounds."""
    # Valid client IDs
    assert (
        validate_and_normalize_correlation_id("my-custom-cid_123")
        == "my-custom-cid_123"
    )
    assert (
        validate_and_normalize_correlation_id("trace.abc-456:sub")
        == "trace.abc-456:sub"
    )

    # None or whitespace generates fresh evalx- ID
    assert validate_and_normalize_correlation_id(None).startswith("evalx-")
    assert validate_and_normalize_correlation_id("   ").startswith("evalx-")

    # Injection attacks with CRLF or null bytes are rejected
    assert validate_and_normalize_correlation_id(
        "cid\r\nInjected-Header: evil"
    ).startswith("evalx-")
    assert validate_and_normalize_correlation_id("cid\x00extra").startswith("evalx-")

    # Exceeding length limit is safely regenerated
    oversized = "a" * (CORRELATION_ID_MAX_LENGTH + 10)
    assert validate_and_normalize_correlation_id(oversized).startswith("evalx-")


# =============================================================================
# 3. Logging Redaction & Sanitization Tests
# =============================================================================


def test_sensitive_data_redaction() -> None:
    """Verifies credentials, URLs, and tokens are scrubbed from strings."""
    pg_url = (
        "postgresql+psycopg://evalx_user:SuperSecretPassword123@localhost:5432/evalx"
    )
    scrubbed_pg = redact_sensitive_string(pg_url)
    assert "SuperSecretPassword123" not in scrubbed_pg
    assert "[REDACTED]" in scrubbed_pg

    redis_url = "redis://:SecretRedisPass@localhost:6379/0"
    scrubbed_redis = redact_sensitive_string(redis_url)
    assert "SecretRedisPass" not in scrubbed_redis
    assert "[REDACTED]" in scrubbed_redis

    bearer_str = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    scrubbed_bearer = redact_sensitive_string(bearer_str)
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in scrubbed_bearer
    assert "[REDACTED_TOKEN]" in scrubbed_bearer


def test_sensitive_logging_filter() -> None:
    """Verifies that SensitiveDataFilter intercepts and redacts log records."""
    filt = SensitiveDataFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Connected with postgresql://usr:pass12345@db:5432/db",
        args=(),
        exc_info=None,
    )
    filt.filter(record)
    assert "pass12345" not in record.msg
    assert "[REDACTED]" in record.msg


def test_sanitization_omits_prompts_and_candidate_responses() -> None:
    """Verifies raw prompts and responses are omitted from observability metadata."""
    payload = {
        "prompt": "What is the secret recipe?",
        "response": "The secret recipe is flour and water.",
        "actual_output": "The candidate output text.",
        "api_key": "sk-1234567890",
        "nested": {
            "expected_output": "Golden recipe",
            "safe_metric": "factuality",
            "score": 0.95,
        },
    }
    sanitized = sanitize_for_observability(payload, omit_prompts=True)
    assert sanitized["prompt"] == "[OMITTED]"
    assert sanitized["response"] == "[OMITTED]"
    assert sanitized["actual_output"] == "[OMITTED]"
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["nested"]["expected_output"] == "[OMITTED]"
    assert sanitized["nested"]["safe_metric"] == "factuality"
    assert sanitized["nested"]["score"] == 0.95


# =============================================================================
# 4. Durable Audit Persistence & Immutability Tests
# =============================================================================


@pytest.mark.asyncio
async def test_audit_event_persistence_and_retrieval() -> None:
    """Verifies that record_audit_event writes durable records to PostgreSQL."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    unique_suffix = uuid.uuid4().hex[:8]
    test_cid = f"test-cid-{unique_suffix}"

    async with session_factory() as session:
        user = User(
            id=uuid.uuid4(),
            external_auth_id=f"audit-pers-{unique_suffix}",
            email=f"pers-{unique_suffix}@evalx.test",
        )
        session.add(user)
        await session.commit()
        test_user_id = user.id

        # Create an audit event
        await audit_service.record_audit_event(
            session=session,
            event_type=EvaluationEventType.RUN_CREATED,
            correlation_id=test_cid,
            resource_type="run",
            resource_id="run-123",
            actor_user_id=test_user_id,
            owner_user_id=test_user_id,
            outcome="success",
            duration_ms=42.5,
            metadata={"cases": 10, "prompt": "This should be omitted"},
        )
        await session.commit()

        # Retrieve and verify
        stmt = select(AuditEvent).where(AuditEvent.correlation_id == test_cid)
        record = (await session.scalars(stmt)).one()

        assert record.event_type == EvaluationEventType.RUN_CREATED.value
        assert record.correlation_id == test_cid
        assert record.actor_user_id == test_user_id
        assert record.owner_user_id == test_user_id
        assert record.resource_type == "run"
        assert record.outcome == "success"
        assert record.duration_ms == 42.5
        assert record.metadata_payload is not None
        assert record.metadata_payload["cases"] == 10
        assert record.metadata_payload["prompt"] == "[OMITTED]"

    await engine.dispose()


@pytest.mark.asyncio
async def test_audit_event_immutability_and_set_null_semantics() -> None:
    """Verifies deleting referenced user preserves audit event (SET NULL)."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        # 1. Create a user
        unique_suffix = uuid.uuid4().hex[:8]
        user = User(
            id=uuid.uuid4(),
            external_auth_id=f"user-audit-del-{unique_suffix}",
            email=f"del-{unique_suffix}@evalx.test",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        # 2. Record an audit event referencing this user
        test_cid = f"test-immut-{unique_suffix}"
        ev = await audit_service.record_audit_event(
            session=session,
            event_type=EvaluationEventType.CONFIG_CREATED,
            correlation_id=test_cid,
            resource_type="config",
            resource_id="cfg-1",
            actor_user_id=user.id,
            owner_user_id=user.id,
            outcome="success",
        )
        await session.commit()
        ev_id = ev.id

        # 3. Delete the user
        await session.delete(user)
        await session.commit()

        # 4. Verify audit record STILL EXISTS and its FKs are safely set to NULL
        session.expire_all()
        retrieved_ev = await session.get(AuditEvent, ev_id)
        assert retrieved_ev is not None, (
            "Historical audit event was deleted when user was deleted!"
        )
        assert retrieved_ev.actor_user_id is None, "FK should be SET NULL"
        assert retrieved_ev.owner_user_id is None, "FK should be SET NULL"
        assert retrieved_ev.correlation_id == test_cid

    await engine.dispose()


# =============================================================================
# 5. API Endpoints: Audit List & Run Audit Trail Tests
# =============================================================================


@pytest.mark.asyncio
async def test_list_audit_events_endpoint_and_pagination() -> None:
    """Tests GET /api/v1/audit-events with filtering and deterministic pagination."""
    unique_user = f"audit-lister-{uuid.uuid4().hex[:6]}"

    async with managed_audit_client(user_id=unique_user) as client:
        # Create events directly via service
        test_url = require_test_database_url(settings)
        engine = create_async_engine(test_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as session:
            user_stmt = select(User).where(User.external_auth_id == unique_user)
            user_rec = (await session.scalars(user_stmt)).first()
            if not user_rec:
                user_rec = User(
                    id=uuid.uuid4(),
                    external_auth_id=unique_user,
                    email=f"{unique_user}@evalx.test",
                )
                session.add(user_rec)
                await session.commit()

            owner_id = user_rec.id

            for i in range(5):
                await audit_service.record_audit_event(
                    session=session,
                    event_type=EvaluationEventType.RUN_CREATED
                    if i % 2 == 0
                    else EvaluationEventType.RUN_COMPLETED,
                    correlation_id=f"corr-batch-{i}",
                    resource_type="run",
                    resource_id=f"run-batch-{i}",
                    actor_user_id=owner_id,
                    owner_user_id=owner_id,
                    outcome="success",
                )
            await session.commit()
        await engine.dispose()

        # Fetch audit events
        resp = await client.get("/api/v1/audit-events?page=1&page_size=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 1
        assert data["page_size"] == 3
        assert data["total"] >= 5
        assert len(data["items"]) == 3
        assert "X-Correlation-ID" in resp.headers

        # Filter by event_type
        filtered_resp = await client.get("/api/v1/audit-events?event_type=run_created")
        assert filtered_resp.status_code == 200
        f_data = filtered_resp.json()
        for item in f_data["items"]:
            assert item["event_type"] == "run_created"


@pytest.mark.asyncio
async def test_run_audit_trail_chronological_ordering() -> None:
    """Tests GET /api/v1/evaluations/runs/{run_id}/audit for chronological trail."""
    unique_user = f"audit-trail-user-{uuid.uuid4().hex[:6]}"

    async with managed_audit_client(user_id=unique_user) as client:
        test_url = require_test_database_url(settings)
        engine = create_async_engine(test_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        run_id = uuid.uuid4()
        async with session_factory() as session:
            # Provision user, dataset, evaluation, run
            user = User(
                id=uuid.uuid4(),
                external_auth_id=unique_user,
                email=f"{unique_user}@evalx.test",
            )
            session.add(user)
            await session.flush()

            ds = Dataset(
                id=uuid.uuid4(),
                name="Audit Test Dataset",
                description="test",
                owner_user_id=user.id,
            )
            session.add(ds)
            await session.flush()

            eval_def = Evaluation(
                id=uuid.uuid4(),
                name="Audit Eval",
                dataset_id=ds.id,
                model_provider="mock",
                model_name="mock-model",
            )
            session.add(eval_def)
            await session.flush()

            run = EvaluationRun(
                id=run_id,
                evaluation_id=eval_def.id,
                status="completed",
                dataset_version=1,
                dataset_snapshot_hash="h123",
                total_cases=1,
                completed_cases=1,
                failed_cases=0,
            )
            session.add(run)
            await session.flush()

            # Record chronological audit events
            t0 = datetime.now(UTC) - timedelta(seconds=10)
            t1 = datetime.now(UTC) - timedelta(seconds=5)
            t2 = datetime.now(UTC)

            await audit_service.record_audit_event(
                session=session,
                event_type=EvaluationEventType.RUN_CREATED,
                correlation_id="cid-trail",
                resource_type="run",
                resource_id=str(run_id),
                run_id=run_id,
                owner_user_id=user.id,
                outcome="success",
                timestamp=t0,
            )
            await audit_service.record_audit_event(
                session=session,
                event_type=EvaluationEventType.RUN_STARTED,
                correlation_id="cid-trail",
                resource_type="run",
                resource_id=str(run_id),
                run_id=run_id,
                owner_user_id=user.id,
                outcome="success",
                timestamp=t1,
            )
            await audit_service.record_audit_event(
                session=session,
                event_type=EvaluationEventType.RUN_COMPLETED,
                correlation_id="cid-trail",
                resource_type="run",
                resource_id=str(run_id),
                run_id=run_id,
                owner_user_id=user.id,
                outcome="success",
                timestamp=t2,
            )
            await session.commit()
        await engine.dispose()

        # Query run audit trail
        resp = await client.get(f"/api/v1/evaluations/runs/{run_id}/audit")
        assert resp.status_code == 200
        trail = resp.json()
        assert trail["run_id"] == str(run_id)
        assert trail["total_events"] == 3

        events = trail["events"]
        assert events[0]["event_type"] == "run_created"
        assert events[1]["event_type"] == "run_started"
        assert events[2]["event_type"] == "run_completed"


@pytest.mark.asyncio
async def test_cross_tenant_audit_isolation_returns_404() -> None:
    """Verifies that querying another tenant's run audit trail returns 404 Not Found."""
    tenant_a = f"tenant-a-{uuid.uuid4().hex[:6]}"
    tenant_b = f"tenant-b-{uuid.uuid4().hex[:6]}"

    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    target_run_id = uuid.uuid4()
    async with session_factory() as session:
        user_b = User(
            id=uuid.uuid4(),
            external_auth_id=tenant_b,
            email=f"{tenant_b}@evalx.test",
        )
        session.add(user_b)
        await session.flush()

        ds_b = Dataset(
            id=uuid.uuid4(),
            name="Tenant B Dataset",
            owner_user_id=user_b.id,
        )
        session.add(ds_b)
        await session.flush()

        eval_b = Evaluation(
            id=uuid.uuid4(),
            name="Tenant B Eval",
            dataset_id=ds_b.id,
            model_provider="mock",
            model_name="mock-model",
        )
        session.add(eval_b)
        await session.flush()

        run_b = EvaluationRun(
            id=target_run_id,
            evaluation_id=eval_b.id,
            status="completed",
            dataset_version=1,
            dataset_snapshot_hash="hash-b",
        )
        session.add(run_b)
        await session.commit()
    await engine.dispose()

    # Tenant A attempts to access Tenant B's run audit trail
    async with managed_audit_client(user_id=tenant_a) as client_a:
        resp = await client_a.get(f"/api/v1/evaluations/runs/{target_run_id}/audit")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Evaluation run not found"


# =============================================================================
# 6. Metrics Collector & Metrics API Tests
# =============================================================================


def test_metrics_collector_bounded_labels_and_counters() -> None:
    """Verifies thread-safe metrics accumulation and bounded labels."""
    collector = MetricsCollector()

    # Record API requests
    collector.record_request(method="GET", status_code=200, duration_ms=25.0)
    collector.record_request(method="POST", status_code=400, duration_ms=15.0)
    collector.record_request(method="POST", status_code=500, duration_ms=50.0)

    # Record events
    collector.record_event(
        EvaluationEvent(
            event_type=EvaluationEventType.RUN_CREATED,
            correlation_id="c1",
        )
    )
    collector.record_event(
        EvaluationEvent(
            event_type=EvaluationEventType.RUN_COMPLETED,
            correlation_id="c1",
            duration_ms=1200.0,
        )
    )
    collector.record_event(
        EvaluationEvent(
            event_type=EvaluationEventType.EVALUATOR_COMPLETED,
            correlation_id="c1",
            evaluator="factuality",
            duration_ms=300.0,
        )
    )

    # Record security
    collector.record_rate_limit_hit()
    collector.record_rate_limiter_degraded()
    collector.record_request_body_rejection()

    snapshot = collector.get_snapshot()

    # Verify API
    assert snapshot["api"]["requests_total"] == 3
    assert snapshot["api"]["status_4xx_total"] == 1
    assert snapshot["api"]["status_5xx_total"] == 1
    assert snapshot["api"]["methods"]["GET"] == 1
    assert snapshot["api"]["methods"]["POST"] == 2

    # Verify Evaluation
    assert snapshot["evaluation"]["runs_created_total"] == 1
    assert snapshot["evaluation"]["runs_completed_total"] == 1
    assert snapshot["evaluation"]["evaluator_success_total"] == 1
    assert snapshot["evaluation"]["evaluator_types"]["factuality"] == 1

    # Verify Security
    assert snapshot["security"]["rate_limit_hits_total"] == 1
    assert snapshot["security"]["rate_limiter_degraded_total"] == 1
    assert snapshot["security"]["request_body_rejections_total"] == 1


@pytest.mark.asyncio
async def test_get_metrics_endpoint() -> None:
    """Tests GET /api/v1/metrics endpoint."""
    async with managed_audit_client() as client:
        resp = await client.get("/api/v1/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "api" in data
        assert "evaluation" in data
        assert "queue" in data
        assert "security" in data
        assert "requests_total" in data["api"]


# =============================================================================
# 7. Retention & Pruning Tests
# =============================================================================


@pytest.mark.asyncio
async def test_prune_audit_events_retention() -> None:
    """Tests bounded pruning of audit events older than N days."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        # Provision a valid run so fk_audit_events_run_id is satisfied
        unique_suffix = uuid.uuid4().hex[:8]
        user = User(
            id=uuid.uuid4(),
            external_auth_id=f"prune-user-{unique_suffix}",
            email=f"prune-{unique_suffix}@evalx.test",
        )
        session.add(user)
        await session.flush()

        ds = Dataset(
            id=uuid.uuid4(),
            name=f"Prune Dataset {unique_suffix}",
            owner_user_id=user.id,
        )
        session.add(ds)
        await session.flush()

        eval_def = Evaluation(
            id=uuid.uuid4(),
            name=f"Prune Eval {unique_suffix}",
            dataset_id=ds.id,
            model_provider="mock",
            model_name="mock-model",
        )
        session.add(eval_def)
        await session.flush()

        valid_run = EvaluationRun(
            id=uuid.uuid4(),
            evaluation_id=eval_def.id,
            status="completed",
            dataset_version=1,
            dataset_snapshot_hash="hash-prune",
        )
        session.add(valid_run)
        await session.flush()

        # Create an old event (100 days old) without run_id
        old_time = datetime.now(UTC) - timedelta(days=100)
        ev_old = await audit_service.record_audit_event(
            session=session,
            event_type=EvaluationEventType.REQUEST_COMPLETED,
            correlation_id="old-cid",
            resource_type="request",
            timestamp=old_time,
        )

        # Create old event with run_id (preserved when preserve_run_audits=True)
        ev_run_old = await audit_service.record_audit_event(
            session=session,
            event_type=EvaluationEventType.RUN_COMPLETED,
            correlation_id="old-run-cid",
            resource_type="run",
            resource_id=str(valid_run.id),
            run_id=valid_run.id,
            timestamp=old_time,
        )

        # Create a fresh event (today)
        ev_fresh = await audit_service.record_audit_event(
            session=session,
            event_type=EvaluationEventType.REQUEST_COMPLETED,
            correlation_id="fresh-cid",
            resource_type="request",
        )
        await session.commit()

        # Prune events older than 90 days with preserve_run_audits=True
        pruned_count = await audit_service.prune_audit_events(
            session=session,
            older_than_days=90,
            preserve_run_audits=True,
        )
        assert pruned_count >= 1

        # Verify old request event was deleted
        assert (await session.get(AuditEvent, ev_old.id)) is None

        # Verify old run event was PRESERVED
        assert (await session.get(AuditEvent, ev_run_old.id)) is not None

        # Verify fresh event was PRESERVED
        assert (await session.get(AuditEvent, ev_fresh.id)) is not None

    await engine.dispose()
