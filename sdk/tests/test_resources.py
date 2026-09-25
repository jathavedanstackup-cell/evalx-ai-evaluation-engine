"""Unit tests for all typed SDK resource clients using mocked transports."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from evalx.client import EvalXClient
from evalx.config import EvalXConfig
from evalx.models import (
    Dataset,
    DatasetCase,
    EvaluationConfig,
    EvaluationRun,
    EvaluationSchedule,
    GateRule,
    RegressionGate,
)


def _mock_client(handler_fn: Any) -> EvalXClient:
    mock_transport = httpx.MockTransport(handler_fn)
    client = httpx.Client(transport=mock_transport)
    cfg = EvalXConfig(base_url="http://testserver", api_key="test-key")
    return EvalXClient(config=cfg, http_client=client)


def test_datasets_resource_crud() -> None:
    ds_id = str(uuid.uuid4())
    case_id = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path

        if method == "POST" and path == "/api/v1/datasets":
            return httpx.Response(
                201,
                json={
                    "id": ds_id,
                    "name": "Test Dataset",
                    "description": "Desc",
                    "case_count": 0,
                    "version": 1,
                    "created_at": "2026-09-22T12:00:00Z",
                    "updated_at": "2026-09-22T12:00:00Z",
                },
            )
        elif method == "GET" and path == f"/api/v1/datasets/{ds_id}":
            return httpx.Response(
                200,
                json={
                    "id": ds_id,
                    "name": "Test Dataset",
                    "description": "Desc",
                    "case_count": 1,
                    "version": 1,
                    "created_at": "2026-09-22T12:00:00Z",
                    "updated_at": "2026-09-22T12:00:00Z",
                },
            )
        elif method == "POST" and path == f"/api/v1/datasets/{ds_id}/cases":
            return httpx.Response(
                201,
                json={
                    "id": case_id,
                    "dataset_id": ds_id,
                    "input": "test input",
                    "expected_output": "test output",
                    "context": None,
                    "metadata": {},
                    "created_at": "2026-09-22T12:00:00Z",
                    "updated_at": "2026-09-22T12:00:00Z",
                },
            )
        elif method == "DELETE" and path == f"/api/v1/datasets/{ds_id}":
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "Not found"})

    client = _mock_client(handler)

    # 1. Create dataset
    ds = client.datasets.create(name="Test Dataset", description="Desc")
    assert isinstance(ds, Dataset)
    assert str(ds.id) == ds_id
    assert ds.name == "Test Dataset"

    # 2. Get dataset
    fetched = client.datasets.get(ds_id)
    assert str(fetched.id) == ds_id

    # 3. Create case
    case = client.datasets.create_case(
        dataset_id=ds_id,
        input="test input",
        expected_output="test output",
    )
    assert isinstance(case, DatasetCase)
    assert str(case.id) == case_id
    assert case.input == "test input"

    # 4. Delete dataset
    client.datasets.delete(ds_id)


def test_evaluations_resource_crud_and_comparison() -> None:
    eval_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if (
            path in ("/api/v1/evaluation-runs", "/api/v1/evaluations/runs")
            and request.method == "POST"
        ):
            return httpx.Response(
                201,
                json={
                    "id": run_id,
                    "evaluation_id": eval_id,
                    "status": "QUEUED",
                    "created_at": "2026-09-22T12:00:00Z",
                    "updated_at": "2026-09-22T12:00:00Z",
                },
            )
        elif (
            path
            in (
                f"/api/v1/evaluation-runs/{run_id}",
                f"/api/v1/evaluations/runs/{run_id}",
            )
            and request.method == "GET"
        ):
            return httpx.Response(
                200,
                json={
                    "id": run_id,
                    "evaluation_id": eval_id,
                    "status": "COMPLETED",
                    "aggregate_score": 0.95,
                    "total_cases": 10,
                    "passed_cases": 9,
                    "failed_cases": 1,
                    "created_at": "2026-09-22T12:00:00Z",
                    "updated_at": "2026-09-22T12:00:00Z",
                },
            )
        elif path in (
            f"/api/v1/evaluation-runs/{run_id}/results",
            f"/api/v1/evaluations/runs/{run_id}/results",
        ):
            return httpx.Response(
                200,
                json={
                    "run_id": run_id,
                    "status": "COMPLETED",
                    "aggregate_score": 0.95,
                    "results": [],
                },
            )
        elif path in (
            "/api/v1/evaluation-runs/compare",
            "/api/v1/evaluations/runs/compare",
        ):
            return httpx.Response(
                200,
                json={
                    "baseline_run_id": eval_id,
                    "candidate_run_id": run_id,
                    "baseline_aggregate_score": 0.85,
                    "candidate_aggregate_score": 0.95,
                    "score_difference": 0.10,
                    "metrics": {},
                    "improved_cases": [],
                    "regressed_cases": [],
                    "unchanged_cases": [],
                },
            )
        return httpx.Response(404, json={"detail": "Not found"})

    client = _mock_client(handler)

    run = client.evaluations.runs.create(evaluation_id=eval_id)
    assert isinstance(run, EvaluationRun)
    assert str(run.id) == run_id

    fetched = client.evaluations.runs.get(run_id)
    assert fetched.status == "COMPLETED"
    assert fetched.aggregate_score == 0.95

    results = client.evaluations.runs.get_results(run_id)
    assert results.aggregate_score == 0.95

    comparison = client.evaluations.compare(
        baseline_run_id=eval_id,
        candidate_run_id=run_id,
    )
    assert comparison.score_difference == 0.10


def test_configurations_and_regression_gates() -> None:
    cfg_id = str(uuid.uuid4())
    gate_id = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/evaluation-configs" and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": cfg_id,
                    "name": "Standard Config",
                    "version": 1,
                    "evaluators": [],
                    "created_at": "2026-09-22T12:00:00Z",
                    "updated_at": "2026-09-22T12:00:00Z",
                },
            )
        elif path == "/api/v1/regression-gates" and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": gate_id,
                    "name": "Production Gate",
                    "evaluation_id": cfg_id,
                    "version": 1,
                    "rules": [
                        {"metric": "factuality", "operator": ">=", "threshold": 0.8}
                    ],
                    "created_at": "2026-09-22T12:00:00Z",
                    "updated_at": "2026-09-22T12:00:00Z",
                },
            )
        return httpx.Response(404, json={"detail": "Not found"})

    client = _mock_client(handler)

    config = client.configurations.create(
        name="Standard Config",
        evaluators=[],
    )
    assert isinstance(config, EvaluationConfig)
    assert str(config.id) == cfg_id

    gate = client.regression_gates.create(
        name="Production Gate",
        evaluation_id=cfg_id,
        rules=[GateRule(metric="factuality", operator=">=", threshold=0.8)],
    )
    assert isinstance(gate, RegressionGate)
    assert str(gate.id) == gate_id
    assert len(gate.rules) == 1


def test_schedules_and_audit() -> None:
    sched_id = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/evaluation-schedules" and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": sched_id,
                    "name": "Nightly Run",
                    "enabled": True,
                    "schedule_type": "daily",
                    "schedule_definition": {"time_of_day": "00:00"},
                    "dataset_id": str(uuid.uuid4()),
                    "configuration_id": str(uuid.uuid4()),
                    "created_at": "2026-09-22T12:00:00Z",
                    "updated_at": "2026-09-22T12:00:00Z",
                },
            )
        elif path == "/api/v1/audit/metrics" or path == "/api/v1/metrics":
            return httpx.Response(
                200,
                json={
                    "total_runs_completed": 100,
                    "total_runs_failed": 2,
                    "total_evaluations": 15,
                    "snapshot_at": "2026-09-22T12:00:00Z",
                },
            )
        return httpx.Response(404, json={"detail": "Not found"})

    client = _mock_client(handler)

    sched = client.schedules.create(
        name="Nightly Run",
        schedule_type="daily",
        schedule_definition={"time_of_day": "00:00"},
        dataset_id=uuid.uuid4(),
        configuration_id=uuid.uuid4(),
    )
    assert isinstance(sched, EvaluationSchedule)
    assert str(sched.id) == sched_id

    metrics = client.audit.get_metrics()
    assert metrics.total_runs_completed == 100
    assert metrics.total_runs_failed == 2
