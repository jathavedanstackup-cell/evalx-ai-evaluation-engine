"""Evaluation schedules resource clients (synchronous and asynchronous)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from evalx.models.schedule import EvaluationSchedule, ScheduleExecution
from evalx.pagination import Page
from evalx.resources.base import AsyncResource, SyncResource


class SchedulesResource(SyncResource):
    """Synchronous resource client for recurring evaluation schedules."""

    def create(
        self,
        name: str,
        schedule_type: str,
        schedule_definition: dict[str, Any],
        dataset_id: str | UUID,
        configuration_id: str | UUID,
        *,
        configuration_version: int | None = None,
        baseline_run_id: str | UUID | None = None,
        gate_id: str | UUID | None = None,
        enabled: bool = True,
        description: str | None = None,
    ) -> EvaluationSchedule:
        payload: dict[str, Any] = {
            "name": name,
            "schedule_type": schedule_type,
            "schedule_definition": schedule_definition,
            "dataset_id": str(dataset_id),
            "configuration_id": str(configuration_id),
            "enabled": enabled,
            "description": description,
        }
        if configuration_version is not None:
            payload["configuration_version"] = configuration_version
        if baseline_run_id:
            payload["baseline_run_id"] = str(baseline_run_id)
        if gate_id:
            payload["gate_id"] = str(gate_id)

        data = self._transport.request("POST", "evaluation-schedules", json=payload)
        return EvaluationSchedule.model_validate(data)

    def get(self, schedule_id: str | UUID) -> EvaluationSchedule:
        data = self._transport.request("GET", f"evaluation-schedules/{schedule_id}")
        return EvaluationSchedule.model_validate(data)

    def list(
        self,
        *,
        enabled: bool | None = None,
        schedule_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[EvaluationSchedule]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if enabled is not None:
            params["enabled"] = str(enabled).lower()
        if schedule_type:
            params["schedule_type"] = schedule_type

        data = self._transport.request("GET", "evaluation-schedules", params=params)
        items = [
            EvaluationSchedule.model_validate(item) for item in data.get("items", [])
        ]
        return Page[EvaluationSchedule](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    def update(
        self,
        schedule_id: str | UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        schedule_definition: dict[str, Any] | None = None,
        configuration_version: int | None = None,
    ) -> EvaluationSchedule:
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if schedule_definition is not None:
            payload["schedule_definition"] = schedule_definition
        if configuration_version is not None:
            payload["configuration_version"] = configuration_version

        data = self._transport.request(
            "PATCH", f"evaluation-schedules/{schedule_id}", json=payload
        )
        return EvaluationSchedule.model_validate(data)

    def delete(self, schedule_id: str | UUID) -> None:
        self._transport.request("DELETE", f"evaluation-schedules/{schedule_id}")

    def enable(self, schedule_id: str | UUID) -> EvaluationSchedule:
        data = self._transport.request(
            "POST", f"evaluation-schedules/{schedule_id}/enable"
        )
        return EvaluationSchedule.model_validate(data)

    def disable(self, schedule_id: str | UUID) -> EvaluationSchedule:
        data = self._transport.request(
            "POST", f"evaluation-schedules/{schedule_id}/disable"
        )
        return EvaluationSchedule.model_validate(data)

    def trigger(self, schedule_id: str | UUID) -> ScheduleExecution:
        data = self._transport.request(
            "POST", f"evaluation-schedules/{schedule_id}/trigger"
        )
        return ScheduleExecution.model_validate(data)

    def list_executions(
        self,
        schedule_id: str | UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[ScheduleExecution]:
        params = {"page": page, "page_size": page_size}
        data = self._transport.request(
            "GET", f"evaluation-schedules/{schedule_id}/executions", params=params
        )
        items = [
            ScheduleExecution.model_validate(item) for item in data.get("items", [])
        ]
        return Page[ScheduleExecution](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    def get_execution(
        self,
        schedule_id: str | UUID,
        execution_id: str | UUID,
    ) -> ScheduleExecution:
        data = self._transport.request(
            "GET", f"evaluation-schedules/{schedule_id}/executions/{execution_id}"
        )
        return ScheduleExecution.model_validate(data)


class AsyncSchedulesResource(AsyncResource):
    """Asynchronous resource client for recurring evaluation schedules."""

    async def create(
        self,
        name: str,
        schedule_type: str,
        schedule_definition: dict[str, Any],
        dataset_id: str | UUID,
        configuration_id: str | UUID,
        *,
        configuration_version: int | None = None,
        baseline_run_id: str | UUID | None = None,
        gate_id: str | UUID | None = None,
        enabled: bool = True,
        description: str | None = None,
    ) -> EvaluationSchedule:
        payload: dict[str, Any] = {
            "name": name,
            "schedule_type": schedule_type,
            "schedule_definition": schedule_definition,
            "dataset_id": str(dataset_id),
            "configuration_id": str(configuration_id),
            "enabled": enabled,
            "description": description,
        }
        if configuration_version is not None:
            payload["configuration_version"] = configuration_version
        if baseline_run_id:
            payload["baseline_run_id"] = str(baseline_run_id)
        if gate_id:
            payload["gate_id"] = str(gate_id)

        data = await self._transport.request(
            "POST", "evaluation-schedules", json=payload
        )
        return EvaluationSchedule.model_validate(data)

    async def get(self, schedule_id: str | UUID) -> EvaluationSchedule:
        data = await self._transport.request(
            "GET", f"evaluation-schedules/{schedule_id}"
        )
        return EvaluationSchedule.model_validate(data)

    async def list(
        self,
        *,
        enabled: bool | None = None,
        schedule_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[EvaluationSchedule]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if enabled is not None:
            params["enabled"] = str(enabled).lower()
        if schedule_type:
            params["schedule_type"] = schedule_type

        data = await self._transport.request(
            "GET", "evaluation-schedules", params=params
        )
        items = [
            EvaluationSchedule.model_validate(item) for item in data.get("items", [])
        ]
        return Page[EvaluationSchedule](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    async def update(
        self,
        schedule_id: str | UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        schedule_definition: dict[str, Any] | None = None,
        configuration_version: int | None = None,
    ) -> EvaluationSchedule:
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if schedule_definition is not None:
            payload["schedule_definition"] = schedule_definition
        if configuration_version is not None:
            payload["configuration_version"] = configuration_version

        data = await self._transport.request(
            "PATCH", f"evaluation-schedules/{schedule_id}", json=payload
        )
        return EvaluationSchedule.model_validate(data)

    async def delete(self, schedule_id: str | UUID) -> None:
        await self._transport.request("DELETE", f"evaluation-schedules/{schedule_id}")

    async def enable(self, schedule_id: str | UUID) -> EvaluationSchedule:
        data = await self._transport.request(
            "POST", f"evaluation-schedules/{schedule_id}/enable"
        )
        return EvaluationSchedule.model_validate(data)

    async def disable(self, schedule_id: str | UUID) -> EvaluationSchedule:
        data = await self._transport.request(
            "POST", f"evaluation-schedules/{schedule_id}/disable"
        )
        return EvaluationSchedule.model_validate(data)

    async def trigger(self, schedule_id: str | UUID) -> ScheduleExecution:
        data = await self._transport.request(
            "POST", f"evaluation-schedules/{schedule_id}/trigger"
        )
        return ScheduleExecution.model_validate(data)

    async def list_executions(
        self,
        schedule_id: str | UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[ScheduleExecution]:
        params = {"page": page, "page_size": page_size}
        data = await self._transport.request(
            "GET", f"evaluation-schedules/{schedule_id}/executions", params=params
        )
        items = [
            ScheduleExecution.model_validate(item) for item in data.get("items", [])
        ]
        return Page[ScheduleExecution](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    async def get_execution(
        self,
        schedule_id: str | UUID,
        execution_id: str | UUID,
    ) -> ScheduleExecution:
        data = await self._transport.request(
            "GET", f"evaluation-schedules/{schedule_id}/executions/{execution_id}"
        )
        return ScheduleExecution.model_validate(data)
