"""Evaluations and runs resource clients (synchronous and asynchronous)."""

from __future__ import annotations

import asyncio
import builtins
import time
from typing import Any
from uuid import UUID

from evalx.exceptions import EvalXTimeoutError
from evalx.models.analysis import RunAnalysis
from evalx.models.audit import AuditEvent
from evalx.models.evaluation import (
    EvaluationRun,
    EvaluationRunResults,
    RunComparison,
)
from evalx.pagination import Page
from evalx.resources.base import AsyncResource, SyncResource

TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def _build_run_payload(
    evaluation_id: str | UUID | None = None,
    *,
    dataset_id: str | UUID | None = None,
    name: str | None = None,
    config_id: str | UUID | None = None,
    configuration_id: str | UUID | None = None,
    config_version: int | None = None,
    configuration_version: int | None = None,
    case_id: str | UUID | None = None,
    candidate_response: str | None = None,
    candidate_responses: list[str] | None = None,
    responses: list[dict[str, Any]] | dict[str, Any] | None = None,
    evaluators: list[Any] | None = None,
    model_provider: str = "custom",
    model_name: str = "default",
    system_prompt: str | None = None,
    metadata: dict[str, Any] | None = None,
    execution_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_ds = dataset_id or evaluation_id
    payload: dict[str, Any] = {
        "model_provider": model_provider,
        "model_name": model_name,
    }
    if effective_ds is not None:
        payload["dataset_id"] = str(effective_ds)
    if evaluation_id is not None:
        payload["evaluation_id"] = str(evaluation_id)
    if name is not None:
        payload["name"] = name

    effective_cfg = config_id or configuration_id
    if effective_cfg is not None:
        payload["config_id"] = str(effective_cfg)

    effective_ver = (
        config_version if config_version is not None else configuration_version
    )
    if effective_ver is not None:
        payload["config_version"] = effective_ver

    if system_prompt is not None:
        payload["system_prompt"] = system_prompt
    if metadata is not None:
        payload["metadata"] = metadata
    if execution_metadata is not None:
        payload["execution_metadata"] = execution_metadata

    if evaluators is not None:
        specs: list[dict[str, Any]] = []
        for e in evaluators:
            if hasattr(e, "model_dump"):
                d = {
                    "evaluator_type": getattr(e, "evaluator_type", "factuality"),
                    "backend": getattr(e, "backend", "llm_judge"),
                    "weight": getattr(e, "weight", 1.0),
                }
                if getattr(e, "threshold", None) is not None:
                    d["threshold"] = e.threshold
                if getattr(e, "name", None) is not None:
                    d["name"] = e.name
                if getattr(e, "configuration", None) is not None:
                    d["configuration"] = e.configuration
                specs.append(d)
            elif isinstance(e, dict):
                specs.append(e)
        payload["evaluators"] = specs

    if responses is not None:
        payload["responses"] = responses
    elif case_id is not None and candidate_response is not None:
        payload["responses"] = [
            {"case_id": str(case_id), "response": candidate_response}
        ]
    elif case_id is not None and candidate_responses is not None:
        payload["responses"] = [
            {"case_id": str(case_id), "candidate_responses": candidate_responses}
        ]
    elif candidate_response is not None and effective_ds is not None:
        payload["responses"] = [
            {"case_id": str(effective_ds), "response": candidate_response}
        ]

    return payload


class RunsResource(SyncResource):
    """Synchronous client for evaluation runs."""

    def create(
        self,
        evaluation_id: str | UUID | None = None,
        *,
        dataset_id: str | UUID | None = None,
        name: str | None = None,
        config_id: str | UUID | None = None,
        configuration_id: str | UUID | None = None,
        config_version: int | None = None,
        configuration_version: int | None = None,
        case_id: str | UUID | None = None,
        candidate_response: str | None = None,
        candidate_responses: list[str] | None = None,
        input_text: str | None = None,
        context: Any = None,
        parameters: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        responses: list[dict[str, Any]] | dict[str, Any] | None = None,
        evaluators: list[Any] | None = None,
        model_provider: str = "custom",
        model_name: str = "default",
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
        execution_metadata: dict[str, Any] | None = None,
    ) -> EvaluationRun:
        payload = _build_run_payload(
            evaluation_id=evaluation_id,
            dataset_id=dataset_id,
            name=name,
            config_id=config_id,
            configuration_id=configuration_id,
            config_version=config_version,
            configuration_version=configuration_version,
            case_id=case_id,
            candidate_response=candidate_response,
            candidate_responses=candidate_responses,
            responses=responses,
            evaluators=evaluators,
            model_provider=model_provider,
            model_name=model_name,
            system_prompt=system_prompt,
            metadata=metadata,
            execution_metadata=execution_metadata,
        )
        if input_text is not None:
            payload["input_text"] = input_text
        if context is not None:
            payload["context"] = context
        if parameters is not None:
            payload["parameters"] = parameters
        if tags is not None:
            payload["tags"] = tags
        data = self._transport.request("POST", "evaluations/runs", json=payload)
        return EvaluationRun.model_validate(data)

    def get(self, run_id: str | UUID) -> EvaluationRun:
        data = self._transport.request("GET", f"evaluations/runs/{run_id}")
        return EvaluationRun.model_validate(data)

    def list(
        self,
        *,
        evaluation_id: str | UUID | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[EvaluationRun]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if evaluation_id:
            params["evaluation_id"] = str(evaluation_id)
        if status:
            params["status"] = status
        data = self._transport.request("GET", "evaluations/runs", params=params)
        items = [EvaluationRun.model_validate(item) for item in data.get("items", [])]
        return Page[EvaluationRun](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    def get_results(self, run_id: str | UUID) -> EvaluationRunResults:
        data = self._transport.request("GET", f"evaluations/runs/{run_id}/results")
        return EvaluationRunResults.model_validate(data)

    def get_analysis(self, run_id: str | UUID) -> RunAnalysis:
        data = self._transport.request("GET", f"evaluations/runs/{run_id}/analysis")
        return RunAnalysis.model_validate(data)

    def get_audit(self, run_id: str | UUID) -> builtins.list[AuditEvent]:
        data = self._transport.request("GET", f"evaluations/runs/{run_id}/audit")
        if isinstance(data, dict) and "events" in data:
            return [AuditEvent.model_validate(e) for e in data["events"]]
        elif isinstance(data, list):
            return [AuditEvent.model_validate(e) for e in data]
        return []

    def cancel(self, run_id: str | UUID) -> EvaluationRun:
        data = self._transport.request("POST", f"evaluations/runs/{run_id}/cancel")
        return EvaluationRun.model_validate(data)

    def wait_for_completion(
        self,
        run_id: str | UUID,
        *,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 2.0,
    ) -> EvaluationRun:
        """Polls run until completion or failure, with bounded timeout."""
        start_time = time.monotonic()
        while True:
            run = self.get(run_id)
            if run.status.upper() in TERMINAL_STATUSES:
                return run

            elapsed = time.monotonic() - start_time
            if elapsed >= timeout_seconds:
                raise EvalXTimeoutError(
                    f"Timed out waiting for evaluation run {run_id} after "
                    f"{elapsed:.1f}s (last status: {run.status})"
                )

            sleep_time = min(poll_interval_seconds, timeout_seconds - elapsed)
            time.sleep(max(0.1, sleep_time))


class EvaluationsResource(SyncResource):
    """Synchronous resource client for evaluations and run operations."""

    def __init__(self, transport: Any) -> None:
        super().__init__(transport)
        self.runs = RunsResource(transport)

    def compare(
        self,
        baseline_run_id: str | UUID,
        candidate_run_id: str | UUID,
    ) -> RunComparison:
        params = {
            "baseline_run_id": str(baseline_run_id),
            "candidate_run_id": str(candidate_run_id),
            "base_run_id": str(baseline_run_id),
            "target_run_id": str(candidate_run_id),
        }
        data = self._transport.request("GET", "evaluations/runs/compare", params=params)
        return RunComparison.model_validate(data)


class AsyncRunsResource(AsyncResource):
    """Asynchronous client for evaluation runs."""

    async def create(
        self,
        evaluation_id: str | UUID | None = None,
        *,
        dataset_id: str | UUID | None = None,
        name: str | None = None,
        config_id: str | UUID | None = None,
        configuration_id: str | UUID | None = None,
        config_version: int | None = None,
        configuration_version: int | None = None,
        case_id: str | UUID | None = None,
        candidate_response: str | None = None,
        candidate_responses: list[str] | None = None,
        input_text: str | None = None,
        context: Any = None,
        parameters: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        responses: list[dict[str, Any]] | dict[str, Any] | None = None,
        evaluators: list[Any] | None = None,
        model_provider: str = "custom",
        model_name: str = "default",
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
        execution_metadata: dict[str, Any] | None = None,
    ) -> EvaluationRun:
        payload = _build_run_payload(
            evaluation_id=evaluation_id,
            dataset_id=dataset_id,
            name=name,
            config_id=config_id,
            configuration_id=configuration_id,
            config_version=config_version,
            configuration_version=configuration_version,
            case_id=case_id,
            candidate_response=candidate_response,
            candidate_responses=candidate_responses,
            responses=responses,
            evaluators=evaluators,
            model_provider=model_provider,
            model_name=model_name,
            system_prompt=system_prompt,
            metadata=metadata,
            execution_metadata=execution_metadata,
        )
        if input_text is not None:
            payload["input_text"] = input_text
        if context is not None:
            payload["context"] = context
        if parameters is not None:
            payload["parameters"] = parameters
        if tags is not None:
            payload["tags"] = tags
        data = await self._transport.request("POST", "evaluations/runs", json=payload)
        return EvaluationRun.model_validate(data)

    async def get(self, run_id: str | UUID) -> EvaluationRun:
        data = await self._transport.request("GET", f"evaluations/runs/{run_id}")
        return EvaluationRun.model_validate(data)

    async def list(
        self,
        *,
        evaluation_id: str | UUID | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[EvaluationRun]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if evaluation_id:
            params["evaluation_id"] = str(evaluation_id)
        if status:
            params["status"] = status
        data = await self._transport.request("GET", "evaluations/runs", params=params)
        items = [EvaluationRun.model_validate(item) for item in data.get("items", [])]
        return Page[EvaluationRun](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    async def get_results(self, run_id: str | UUID) -> EvaluationRunResults:
        data = await self._transport.request(
            "GET", f"evaluations/runs/{run_id}/results"
        )
        return EvaluationRunResults.model_validate(data)

    async def get_analysis(self, run_id: str | UUID) -> RunAnalysis:
        data = await self._transport.request(
            "GET", f"evaluations/runs/{run_id}/analysis"
        )
        return RunAnalysis.model_validate(data)

    async def get_audit(self, run_id: str | UUID) -> builtins.list[AuditEvent]:
        data = await self._transport.request("GET", f"evaluations/runs/{run_id}/audit")
        if isinstance(data, dict) and "events" in data:
            return [AuditEvent.model_validate(e) for e in data["events"]]
        elif isinstance(data, list):
            return [AuditEvent.model_validate(e) for e in data]
        return []

    async def cancel(self, run_id: str | UUID) -> EvaluationRun:
        data = await self._transport.request(
            "POST", f"evaluations/runs/{run_id}/cancel"
        )
        return EvaluationRun.model_validate(data)

    async def wait_for_completion(
        self,
        run_id: str | UUID,
        *,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 2.0,
    ) -> EvaluationRun:
        """Asynchronously polls run until completion with bounded timeout."""
        start_time = time.monotonic()
        while True:
            run = await self.get(run_id)
            if run.status.upper() in TERMINAL_STATUSES:
                return run

            elapsed = time.monotonic() - start_time
            if elapsed >= timeout_seconds:
                raise EvalXTimeoutError(
                    f"Timed out waiting for evaluation run {run_id} after "
                    f"{elapsed:.1f}s (last status: {run.status})"
                )

            sleep_time = min(poll_interval_seconds, timeout_seconds - elapsed)
            await asyncio.sleep(max(0.1, sleep_time))


class AsyncEvaluationsResource(AsyncResource):
    """Asynchronous resource client for evaluations and run operations."""

    def __init__(self, transport: Any) -> None:
        super().__init__(transport)
        self.runs = AsyncRunsResource(transport)

    async def compare(
        self,
        baseline_run_id: str | UUID,
        candidate_run_id: str | UUID,
    ) -> RunComparison:
        params = {
            "baseline_run_id": str(baseline_run_id),
            "candidate_run_id": str(candidate_run_id),
            "base_run_id": str(baseline_run_id),
            "target_run_id": str(candidate_run_id),
        }
        data = await self._transport.request(
            "GET", "evaluations/runs/compare", params=params
        )
        return RunComparison.model_validate(data)
