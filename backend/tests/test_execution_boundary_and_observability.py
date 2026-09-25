import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.v1.evaluation_runs import get_run_executor
from app.core.config import require_test_database_url, settings
from app.database.session import get_async_session
from app.main import app
from app.observability.correlation import validate_and_normalize_correlation_id
from app.observability.events import EvaluationEvent, EvaluationEventType
from app.observability.observability import EvaluationObservability
from app.observability.sanitization import (
    MAX_METADATA_DEPTH,
    MAX_METADATA_KEYS,
    sanitize_for_observability,
    validate_execution_metadata,
)
from app.schemas.evaluation_run import (
    CaseResponseInput,
    EvaluationRunCreate,
    EvaluatorConfigInput,
)
from app.services.evaluation_run_service import (
    execute_evaluation_run,
    submit_evaluation_run,
)
from app.services.run_executor import SynchronousRunExecutor


@asynccontextmanager
async def managed_api_client() -> AsyncIterator[AsyncClient]:
    """Provides an AsyncClient connected exclusively to the TEST database."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def get_test_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    from app.auth.dependencies import get_auth_provider
    from app.auth.test_provider import TestAuthProvider

    app.dependency_overrides[get_async_session] = get_test_db_session
    app.dependency_overrides[get_auth_provider] = lambda: TestAuthProvider(
        default_user_id="test-user-default"
    )
    app.dependency_overrides[get_run_executor] = lambda: SynchronousRunExecutor()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(
                text(
                    "TRUNCATE TABLE users, datasets, dataset_cases, "
                    "evaluations, evaluator_configs, evaluation_runs, "
                    "evaluation_results CASCADE"
                )
            )
            await cleanup_session.commit()
        await engine.dispose()


async def _create_test_dataset(
    client: AsyncClient, count: int = 2
) -> tuple[str, list[str]]:
    """Helper to create a dataset and bulk ingest test cases."""
    ds_resp = await client.post(
        "/api/v1/datasets",
        json={"name": "Obs Test Dataset", "description": "For observability tests"},
    )
    assert ds_resp.status_code == 201
    dataset_id = ds_resp.json()["id"]

    cases_payload = {
        "cases": [
            {
                "input": f"Question {i}",
                "expected_output": f"Answer {i}",
                "context": [f"Passage {i}"],
                "metadata": {"test_idx": i},
            }
            for i in range(count)
        ]
    }
    cases_resp = await client.post(
        f"/api/v1/datasets/{dataset_id}/cases/bulk",
        json=cases_payload,
    )
    assert cases_resp.status_code == 201
    case_ids = [c["id"] for c in cases_resp.json()["cases"]]
    return dataset_id, case_ids


# =========================================================================
# 1. Correlation ID Generation & Validation Tests
# =========================================================================


def test_validate_or_generate_correlation_id_generated_format() -> None:
    corr_id = validate_and_normalize_correlation_id(None)
    assert corr_id.startswith("evalx-")
    assert len(corr_id) == 22  # "evalx-" (6) + 16 hex chars


def test_validate_or_generate_correlation_id_valid_formats() -> None:
    valid_ids = [
        "req-12345",
        "client.trace_99:node1",
        "abc-DEF-123_456:789.0",
        "simple",
    ]
    for vid in valid_ids:
        assert validate_and_normalize_correlation_id(vid) == vid

    # Trimming whitespace
    assert (
        validate_and_normalize_correlation_id("  req-with-spaces  ")
        == "req-with-spaces"
    )


def test_validate_or_generate_correlation_id_invalid_formats() -> None:
    invalid_ids = [
        None,
        "",
        "   ",
        "bad\r\ninjection",  # CRLF
        "bad\ninjection",  # LF
        "header-with-$pecial*chars",
        "<script>alert(1)</script>",
        "a" * 101,  # Exceeds 100 chars
    ]
    for inv in invalid_ids:
        generated = validate_and_normalize_correlation_id(inv)
        assert generated.startswith("evalx-")
        assert len(generated) == 22


# =========================================================================
# 2. Sanitization & Metadata Bound Tests
# =========================================================================


def test_sanitize_recursive_credentials() -> None:
    payload = {
        "api_key": "sk-secret-12345",
        "nested": {
            "openai_api_key": "sk-proj-xyz",
            "password": "supersecretpassword",
            "token": "bearer-token-val",
            "safe_key": "safe_value",
            "items": [
                {"secret_token": "hidden", "description": "safe"},
                "plain string",
            ],
        },
    }
    sanitized = sanitize_for_observability(payload)
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["nested"]["openai_api_key"] == "[REDACTED]"
    assert sanitized["nested"]["password"] == "[REDACTED]"
    assert sanitized["nested"]["token"] == "[REDACTED]"
    assert sanitized["nested"]["safe_key"] == "safe_value"
    assert sanitized["nested"]["items"][0]["secret_token"] == "[REDACTED]"
    assert sanitized["nested"]["items"][0]["description"] == "safe"
    assert sanitized["nested"]["items"][1] == "plain string"


def test_sanitize_omit_prompts_and_responses() -> None:
    payload = {
        "prompt": "Tell me a story about a secret agent",
        "response": "Here is the sensitive response",
        "input": "User input prompt",
        "context": ["Document 1", "Document 2"],
        "expected_output": "Target truth",
        "evaluator": "exact_match",
        "status": "success",
    }
    # With omit_prompts=True (default in logs/events)
    sanitized = sanitize_for_observability(payload, omit_prompts=True)
    assert sanitized["prompt"] == "[OMITTED]"
    assert sanitized["response"] == "[OMITTED]"
    assert sanitized["input"] == "[OMITTED]"
    assert sanitized["context"] == "[OMITTED]"
    assert sanitized["expected_output"] == "[OMITTED]"
    assert sanitized["evaluator"] == "exact_match"
    assert sanitized["status"] == "success"

    # With omit_prompts=False
    raw = sanitize_for_observability(payload, omit_prompts=False)
    assert raw["prompt"] == "Tell me a story about a secret agent"
    assert raw["response"] == "Here is the sensitive response"


def test_validate_execution_metadata_bounds() -> None:
    assert validate_execution_metadata(None) is None
    valid = {"env": "staging", "worker": "w1", "details": {"region": "us-east"}}
    assert validate_execution_metadata(valid) == valid

    # Exceeding keys
    too_many_keys = {f"k_{i}": i for i in range(MAX_METADATA_KEYS + 1)}
    with pytest.raises(ValueError, match="exceeds maximum of 50 keys"):
        validate_execution_metadata(too_many_keys)

    # Exceeding nesting depth
    too_deep = {"l1": {"l2": {"l3": {"l4": "too deep"}}}}
    with pytest.raises(ValueError, match="exceeds maximum nesting depth"):
        validate_execution_metadata(too_deep)

    # Maximum nesting depth of 3 should pass
    depth_3 = {"l1": {"l2": {"l3": "valid"}}}
    assert validate_execution_metadata(depth_3) == depth_3
    assert MAX_METADATA_DEPTH == 3

    # Exceeding size
    huge_str = "x" * 17000
    with pytest.raises(ValueError, match="exceeds maximum serialized size"):
        validate_execution_metadata({"data": huge_str})


# =========================================================================
# 3. Instance-Based Observability & Exception Containment Tests
# =========================================================================


def test_instance_observability_event_capture() -> None:
    obs = EvaluationObservability()
    captured: list[EvaluationEvent] = []
    obs.add_subscriber(captured.append)

    run_id = uuid.uuid4()
    obs.emit(
        EvaluationEvent(
            event_type=EvaluationEventType.RUN_CREATED,
            correlation_id="test-corr-1",
            run_id=run_id,
        )
    )

    assert len(captured) == 1
    event = captured[0]
    assert event.event_type == EvaluationEventType.RUN_CREATED
    assert event.correlation_id == "test-corr-1"
    assert event.run_id == run_id


def test_instance_observability_subscriber_failure_containment() -> None:
    obs = EvaluationObservability()

    def buggy_subscriber(_: EvaluationEvent) -> None:
        raise RuntimeError("Subscriber explosion!")

    captured: list[EvaluationEvent] = []
    obs.add_subscriber(buggy_subscriber)
    obs.add_subscriber(captured.append)

    # emit must NOT raise despite buggy_subscriber throwing RuntimeError
    obs.emit(
        EvaluationEvent(
            event_type=EvaluationEventType.RUN_STARTED,
            correlation_id="test-corr-2",
        )
    )
    assert len(captured) == 1


def test_instance_observability_isolation() -> None:
    obs1 = EvaluationObservability()
    obs2 = EvaluationObservability()

    captured1: list[EvaluationEvent] = []
    captured2: list[EvaluationEvent] = []

    obs1.add_subscriber(captured1.append)
    obs2.add_subscriber(captured2.append)

    obs1.emit(
        EvaluationEvent(
            event_type=EvaluationEventType.RUN_CREATED,
            correlation_id="obs1-corr",
        )
    )

    assert len(captured1) == 1
    assert len(captured2) == 0


# =========================================================================
# 4. Service-Level Execution Boundary Tests
# =========================================================================


@pytest.mark.asyncio
async def test_service_submit_and_execute_boundary() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=2)

        obs = EvaluationObservability()
        captured_events: list[EvaluationEvent] = []
        obs.add_subscriber(captured_events.append)

        run_create_data = EvaluationRunCreate(
            dataset_id=uuid.UUID(dataset_id),
            name="Boundary Test Run",
            evaluators=[
                EvaluatorConfigInput(
                    evaluator_type="instruction_following",
                    backend="native",
                    threshold=1.0,
                )
            ],
            responses=[
                CaseResponseInput(
                    case_id=uuid.UUID(case_ids[0]),
                    response="Answer 0",
                ),
                CaseResponseInput(
                    case_id=uuid.UUID(case_ids[1]),
                    response="Wrong",
                ),
            ],
            execution_metadata={"env": "test", "run_type": "boundary_check"},
        )

        # 1. Test submit_evaluation_run
        async with session_factory() as session:
            run = await submit_evaluation_run(
                session=session,
                data=run_create_data,
                correlation_id="boundary-corr-001",
                observability=obs,
            )
            assert run.status == "pending"
            assert run.duration_ms is None
            assert run.correlation_id == "boundary-corr-001"
            assert run.execution_metadata == {
                "env": "test",
                "run_type": "boundary_check",
            }

            # Check RUN_CREATED event was emitted
            assert len(captured_events) == 1
            assert captured_events[0].event_type == EvaluationEventType.RUN_CREATED
            assert captured_events[0].correlation_id == "boundary-corr-001"
            assert captured_events[0].run_id == run.id

            run_id = run.id

        # 2. Test execute_evaluation_run
        async with session_factory() as session:
            executed_run = await execute_evaluation_run(
                session=session,
                run_id=run_id,
                data=run_create_data,
                observability=obs,
            )
            assert executed_run.status == "completed"
            assert executed_run.duration_ms is not None
            assert executed_run.duration_ms > 0.0
            assert executed_run.completed_cases == 1
            assert executed_run.failed_cases == 1

            # Check full event lifecycle
            event_types = [e.event_type for e in captured_events]
            assert event_types == [
                EvaluationEventType.RUN_CREATED,
                EvaluationEventType.RUN_STARTED,
                EvaluationEventType.CASE_EVALUATION_STARTED,
                EvaluationEventType.CASE_EVALUATION_STARTED,
                EvaluationEventType.CASE_EVALUATION_COMPLETED,
                EvaluationEventType.CASE_EVALUATION_COMPLETED,
                EvaluationEventType.RUN_COMPLETED,
            ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_synchronous_run_executor() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=1)

        obs = EvaluationObservability()
        captured_events: list[EvaluationEvent] = []
        obs.add_subscriber(captured_events.append)

        executor = SynchronousRunExecutor(observability=obs)

        run_create_data = EvaluationRunCreate(
            dataset_id=uuid.UUID(dataset_id),
            name="Sync Executor Run",
            evaluators=[
                EvaluatorConfigInput(
                    evaluator_type="instruction_following",
                    backend="native",
                    threshold=1.0,
                )
            ],
            responses=[
                CaseResponseInput(
                    case_id=uuid.UUID(case_ids[0]),
                    response="Answer 0",
                ),
            ],
        )

        async with session_factory() as session:
            completed_run = await executor.submit_and_execute(
                session=session,
                data=run_create_data,
                correlation_id="sync-corr-99",
            )
            assert completed_run.status == "completed"
            assert completed_run.duration_ms is not None
            assert completed_run.duration_ms >= 0.0
            assert completed_run.correlation_id == "sync-corr-99"

            event_types = [e.event_type for e in captured_events]
            assert EvaluationEventType.RUN_CREATED in event_types
            assert EvaluationEventType.RUN_STARTED in event_types
            assert EvaluationEventType.RUN_COMPLETED in event_types

    await engine.dispose()


# =========================================================================
# 5. API End-to-End Correlation & Metadata Integration Tests
# =========================================================================


@pytest.mark.asyncio
async def test_api_correlation_id_custom_header_propagation() -> None:
    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=1)

        custom_id = "trace-client-12345"
        payload = {
            "dataset_id": dataset_id,
            "name": "Correlation Run",
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                }
            ],
            "responses": [
                {"case_id": case_ids[0], "response": "Answer 0"},
            ],
            "execution_metadata": {"batch": 10, "caller": "ci_pipeline"},
        }

        resp = await client.post(
            "/api/v1/evaluations/runs",
            json=payload,
            headers={"X-Correlation-ID": custom_id},
        )
        assert resp.status_code == 201
        data = resp.json()

        # Check response header
        assert resp.headers.get("x-correlation-id") == custom_id

        # Check body fields
        assert data["correlation_id"] == custom_id
        assert data["duration_ms"] is not None
        assert data["duration_ms"] >= 0.0
        assert data["execution_metadata"] == {"batch": 10, "caller": "ci_pipeline"}

        # Verify GET preserves correlation_id and duration_ms
        run_id = data["id"]
        get_resp = await client.get(f"/api/v1/evaluations/runs/{run_id}")
        assert get_resp.status_code == 200
        get_data = get_resp.json()
        assert get_data["correlation_id"] == custom_id
        assert get_data["duration_ms"] == data["duration_ms"]
        assert get_data["execution_metadata"] == {"batch": 10, "caller": "ci_pipeline"}


@pytest.mark.asyncio
async def test_api_correlation_id_generated_when_missing_or_invalid() -> None:
    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=1)

        payload = {
            "dataset_id": dataset_id,
            "name": "Auto Corr Run",
            "evaluators": [
                {
                    "evaluator_type": "instruction_following",
                    "backend": "native",
                }
            ],
            "responses": [
                {"case_id": case_ids[0], "response": "Answer 0"},
            ],
        }

        # Case A: Missing header -> server generates evalx-...
        resp1 = await client.post("/api/v1/evaluations/runs", json=payload)
        assert resp1.status_code == 201
        corr_id1 = resp1.headers.get("x-correlation-id")
        assert corr_id1 is not None and corr_id1.startswith("evalx-")
        assert resp1.json()["correlation_id"] == corr_id1

        # Case B: Malformed header with CRLF injection -> fallback to evalx-...
        resp2 = await client.post(
            "/api/v1/evaluations/runs",
            json=payload,
            headers={"X-Correlation-ID": "bad\r\ninjection"},
        )
        assert resp2.status_code == 201
        corr_id2 = resp2.headers.get("x-correlation-id")
        assert corr_id2 is not None and corr_id2.startswith("evalx-")
        assert "\r" not in corr_id2 and "\n" not in corr_id2
        assert resp2.json()["correlation_id"] == corr_id2


@pytest.mark.asyncio
async def test_api_execution_metadata_validation_rejection() -> None:
    async with managed_api_client() as client:
        dataset_id, case_ids = await _create_test_dataset(client, count=1)

        # 1. More than 50 keys
        too_many_keys = {f"key_{i}": i for i in range(51)}
        bad_payload_keys = {
            "dataset_id": dataset_id,
            "name": "Too Many Keys Run",
            "evaluators": [{"evaluator_type": "instruction_following"}],
            "responses": [{"case_id": case_ids[0], "response": "A"}],
            "execution_metadata": too_many_keys,
        }
        resp1 = await client.post(
            "/api/v1/evaluations/runs",
            json=bad_payload_keys,
        )
        assert resp1.status_code == 422
        assert "exceeds maximum of 50 keys" in resp1.text

        # 2. More than 3 nesting levels
        too_deep = {"l1": {"l2": {"l3": {"l4": "depth 4"}}}}
        bad_payload_depth = {
            "dataset_id": dataset_id,
            "name": "Too Deep Run",
            "evaluators": [{"evaluator_type": "instruction_following"}],
            "responses": [{"case_id": case_ids[0], "response": "A"}],
            "execution_metadata": too_deep,
        }
        resp2 = await client.post(
            "/api/v1/evaluations/runs",
            json=bad_payload_depth,
        )
        assert resp2.status_code == 422
        assert "exceeds maximum nesting depth" in resp2.text
