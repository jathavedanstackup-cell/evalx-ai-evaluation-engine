"""Evaluation configuration and preset models for the EVALX SDK."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from evalx.models.common import EvalXBaseModel


class EvaluatorSpec(EvalXBaseModel):
    """Specification for an individual evaluator within a configuration."""

    evaluator_type: str
    backend: str = "llm_judge"
    threshold: float | None = 0.8
    weight: float = 1.0
    enabled: bool = True
    name: str | None = None
    configuration: dict[str, Any] | None = None

    def __init__(
        self,
        evaluator_type: str,
        *,
        backend: str = "llm_judge",
        threshold: float | None = 0.8,
        weight: float = 1.0,
        enabled: bool = True,
        name: str | None = None,
        configuration: dict[str, Any] | None = None,
        metric_name: str | None = None,
        parameters: dict[str, Any] | None = None,
        **data: Any,
    ) -> None:
        if name is None and metric_name is not None:
            name = metric_name
        if configuration is None and parameters is not None:
            configuration = parameters
        init_data: dict[str, Any] = {
            "evaluator_type": evaluator_type,
            "backend": backend,
            "threshold": threshold,
            "weight": weight,
            "enabled": enabled,
            "name": name,
            "configuration": configuration,
            **data,
        }
        super().__init__(**init_data)

    @model_validator(mode="before")
    @classmethod
    def _normalize_spec(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "name" not in data and "metric_name" in data:
                data["name"] = data["metric_name"]
            if "configuration" not in data and "parameters" in data:
                data["configuration"] = data["parameters"]
        return data

    @property
    def metric_name(self) -> str:
        return self.name or self.evaluator_type

    @property
    def parameters(self) -> dict[str, Any]:
        return self.configuration or {}


class EvaluationConfig(EvalXBaseModel):
    """Represents an evaluation configuration preset."""

    id: UUID
    name: str
    description: str | None = None
    preset_name: str | None = None
    version: int = 1
    owner_user_id: UUID | None = None
    snapshot_hash: str | None = None
    evaluators: list[EvaluatorSpec] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime | None = None


class EvaluationConfigVersion(EvalXBaseModel):
    """Historical snapshot of an evaluation configuration."""

    id: UUID
    config_id: UUID
    version: int
    name: str
    description: str | None = None
    snapshot_hash: str | None = None
    evaluators: list[EvaluatorSpec] = Field(default_factory=list)
    created_at: datetime
