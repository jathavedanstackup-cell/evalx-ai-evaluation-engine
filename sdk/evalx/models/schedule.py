"""Evaluation schedule models for the EVALX SDK."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from evalx.models.common import EvalXBaseModel


class EvaluationSchedule(EvalXBaseModel):
    """Represents an automated recurring evaluation schedule."""

    id: UUID
    name: str
    description: str | None = None
    enabled: bool = True
    schedule_type: str
    schedule_definition: dict[str, Any] = Field(default_factory=dict)
    dataset_id: UUID
    configuration_id: UUID
    configuration_version: int | None = None
    baseline_run_id: UUID | None = None
    gate_id: UUID | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_run_id: UUID | None = None
    last_status: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ScheduleExecution(EvalXBaseModel):
    """Record of a single execution trigger of an evaluation schedule."""

    id: UUID
    schedule_id: UUID
    run_id: UUID | None = None
    execution_status: str = "pending"
    error_code: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    correlation_id: str | None = None
    created_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_execution_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "status" in data and "execution_status" not in data:
                data["execution_status"] = data["status"]
            if "scheduled_for" in data and "started_at" not in data:
                data["started_at"] = data["scheduled_for"]
            if "error_message" in data and "error_code" not in data:
                data["error_code"] = data["error_message"]
        return data

    @property
    def status(self) -> str:
        return self.execution_status

    @property
    def scheduled_for(self) -> datetime:
        return self.started_at

    @property
    def error_message(self) -> str | None:
        return self.error_code
