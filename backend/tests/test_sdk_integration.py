"""End-to-end integration tests for the official EVALX Python SDK."""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure sdk directory is importable
_sdk_path = str(Path(__file__).resolve().parents[2] / "sdk")
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)

from evalx import AsyncEvalXClient, EvalXClient, EvalXConfig  # noqa: E402
from evalx.exceptions import EvalXNotFoundError  # noqa: E402
from evalx.models import EvaluatorSpec, GateRule  # noqa: E402

from app.api.v1.evaluation_runs import get_run_executor  # noqa: E402
from app.auth.dependencies import get_auth_provider  # noqa: E402
from app.auth.test_provider import TestAuthProvider  # noqa: E402
from app.core.config import require_test_database_url, settings  # noqa: E402
from app.database.session import get_async_session  # noqa: E402
from app.main import app  # noqa: E402
from app.services.run_executor import SynchronousRunExecutor  # noqa: E402


@asynccontextmanager
async def managed_sdk_client(
    user_id: str = "sdk-test-tenant",
) -> AsyncIterator[tuple[EvalXClient, AsyncEvalXClient]]:
    """Configures SDK clients connected to FastAPI app via ASGITransport."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def get_test_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    test_provider = TestAuthProvider(default_user_id=None)
    app.dependency_overrides[get_async_session] = get_test_db_session
    app.dependency_overrides[get_auth_provider] = lambda: test_provider
    app.dependency_overrides[get_run_executor] = lambda: SynchronousRunExecutor()

    sync_http = TestClient(app=app, base_url="http://testserver")

    async_asgi = ASGITransport(app=app)
    async_http = httpx.AsyncClient(transport=async_asgi, base_url="http://testserver")

    cfg = EvalXConfig(
        base_url="http://testserver",
        api_key=user_id,
    )
    sync_client = EvalXClient(config=cfg, http_client=sync_http)
    async_client = AsyncEvalXClient(config=cfg, http_client=async_http)

    try:
        yield sync_client, async_client
    finally:
        sync_client.close()
        await async_client.aclose()
        app.dependency_overrides.clear()
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(
                text(
                    "TRUNCATE TABLE users, datasets, dataset_cases, "
                    "evaluations, evaluator_configs, evaluation_runs, "
                    "evaluation_results, evaluation_schedules, "
                    "schedule_executions, regression_gates, "
                    "regression_gate_versions, audit_events CASCADE"
                )
            )
            await cleanup_session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_sdk_datasets_full_lifecycle() -> None:
    async with managed_sdk_client("tenant-alpha") as (client, _):
        # 1. Create dataset
        ds = client.datasets.create(
            name="Customer Inquiries",
            description="Production FAQ evaluation dataset",
        )
        assert ds.name == "Customer Inquiries"
        assert ds.case_count == 0

        # 2. Add test cases
        case1 = client.datasets.create_case(
            dataset_id=ds.id,
            input="How do I reset my password?",
            expected_output="Visit settings and click reset.",
        )
        assert case1.input == "How do I reset my password?"
        assert case1.dataset_id == ds.id

        # 3. Bulk ingest cases
        bulk_res = client.datasets.bulk_create_cases(
            dataset_id=ds.id,
            cases=[
                {"input": "Refund timeline?", "expected_output": "Within 14 days."},
                {"input": "Cancel subscription?", "expected_output": "Click cancel."},
            ],
        )
        assert bulk_res.created_count == 2

        # 4. List cases
        cases_page = client.datasets.list_cases(ds.id, page=1, page_size=10)
        assert cases_page.total == 3
        assert len(cases_page.items) == 3

        # 5. Validate dataset
        validation = client.datasets.validate(ds.id)
        assert validation.is_valid is True
        assert validation.total_cases == 3

        # 6. Update dataset
        updated = client.datasets.update(ds.id, name="Updated Inquiries")
        assert updated.name == "Updated Inquiries"

        # 7. Delete dataset
        client.datasets.delete(ds.id)

        # 8. Verify 404 raises EvalXNotFoundError
        with pytest.raises(EvalXNotFoundError):
            client.datasets.get(ds.id)


@pytest.mark.asyncio
async def test_sdk_evaluation_runs_and_results() -> None:
    async with managed_sdk_client("tenant-runs") as (client, _):
        # Setup dataset and test case
        ds = client.datasets.create(name="Run Dataset")
        case = client.datasets.create_case(
            dataset_id=ds.id,
            input="Tell me a joke",
            expected_output="Why did the chicken cross the road?",
        )

        # Create evaluation configuration
        config = client.configurations.create(
            name="Instruction Following Config",
            evaluators=[
                EvaluatorSpec(
                    evaluator_type="instruction_following",
                    backend="native",
                    threshold=1.0,
                )
            ],
        )

        # Create evaluation run
        run = client.evaluations.runs.create(
            dataset_id=ds.id,
            configuration_id=config.id,
            case_id=case.id,
            candidate_response="Why did the chicken cross the road?",
        )
        assert run.status.lower() in ("completed", "queued", "running")

        # Wait for completion (synchronous runner finishes immediately)
        completed = client.evaluations.runs.wait_for_completion(
            run.id,
            timeout_seconds=5.0,
        )
        assert completed.status.lower() == "completed"
        assert completed.aggregate_score is not None

        # Fetch results
        results = client.evaluations.runs.get_results(run.id)
        assert results.status.lower() == "completed"
        assert len(results.results) >= 1

        # Fetch analysis
        analysis = client.evaluations.runs.get_analysis(run.id)
        assert analysis.total_cases >= 1


@pytest.mark.asyncio
async def test_sdk_schedules_and_regression_gates() -> None:
    async with managed_sdk_client("tenant-sched") as (client, _):
        ds = client.datasets.create(name="Scheduled Dataset")
        client.datasets.create_case(
            dataset_id=ds.id,
            input="Scheduled input",
            expected_output="Scheduled output",
        )
        config = client.configurations.create(
            name="Default Config",
            evaluators=[
                EvaluatorSpec(
                    evaluator_type="instruction_following",
                    backend="native",
                    threshold=1.0,
                )
            ],
        )

        # Create schedule
        sched = client.schedules.create(
            name="Hourly Check",
            schedule_type="interval",
            schedule_definition={"interval_minutes": 15},
            dataset_id=ds.id,
            configuration_id=config.id,
        )
        assert sched.name == "Hourly Check"
        assert sched.enabled is True

        # Disable schedule
        disabled = client.schedules.disable(sched.id)
        assert disabled.enabled is False

        # Enable schedule
        enabled = client.schedules.enable(sched.id)
        assert enabled.enabled is True

        # Trigger schedule
        execution = client.schedules.trigger(sched.id)
        assert execution.schedule_id == sched.id

        # List executions
        execs = client.schedules.list_executions(sched.id)
        assert execs.total >= 1

        # Create regression gate
        eval_id = uuid.uuid4()
        gate = client.regression_gates.create(
            name="Strict Accuracy Gate",
            evaluation_id=eval_id,
            configuration_id=config.id,
            rules=[
                GateRule(
                    metric="exact_match",
                    operator=">=",
                    threshold=0.9,
                )
            ],
        )
        assert gate.name == "Strict Accuracy Gate"
        assert len(gate.rules) == 1


@pytest.mark.asyncio
async def test_sdk_audit_and_metrics() -> None:
    async with managed_sdk_client("tenant-audit") as (client, _):
        # Trigger an action that creates an audit event
        client.datasets.create(name="Audit Test DS")
        config = client.configurations.create(
            name="Audit Test Config",
            evaluators=[
                EvaluatorSpec(
                    evaluator_type="instruction_following",
                    backend="native",
                    threshold=1.0,
                )
            ],
        )

        # Query audit events
        events_page = client.audit.list(page_size=10)
        assert events_page.total >= 1
        assert any(e.resource_id == str(config.id) for e in events_page.items)
        assert any(e.resource_type == "config" for e in events_page.items)

        # Query metrics snapshot
        metrics = client.audit.get_metrics()
        assert metrics.snapshot_at is not None


@pytest.mark.asyncio
async def test_async_sdk_client_workflow() -> None:
    async with managed_sdk_client("async-tenant") as (_, async_client):
        # Test full async resource calls
        ds = await async_client.datasets.create(name="Async Dataset")
        assert ds.name == "Async Dataset"

        case = await async_client.datasets.create_case(
            dataset_id=ds.id,
            input="Async input",
        )
        assert case.dataset_id == ds.id

        page = await async_client.datasets.list()
        assert page.total >= 1

        await async_client.datasets.delete(ds.id)
        with pytest.raises(EvalXNotFoundError):
            await async_client.datasets.get(ds.id)
