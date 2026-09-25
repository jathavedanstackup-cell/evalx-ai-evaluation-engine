"""Regression gate models for the EVALX SDK."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from evalx.models.common import EvalXBaseModel


class GateRule(EvalXBaseModel):
    """Rule defining acceptable performance boundaries for a metric."""

    metric_name: str
    operator: str
    threshold: float
    is_delta: bool = False
    severity: str = "critical"
    enabled: bool = True

    def __init__(
        self,
        metric: str | None = None,
        operator: str = "gte",
        threshold: float = 0.0,
        *,
        metric_name: str | None = None,
        is_delta: bool = False,
        severity: str = "critical",
        enabled: bool = True,
        required: bool = True,
        **data: Any,
    ) -> None:
        final_metric = metric_name or metric or "overall_score"
        op_map = {
            ">=": "gte",
            ">": "gt",
            "<=": "lte",
            "<": "lt",
            "==": "eq",
            "=": "eq",
        }
        final_op = op_map.get(operator, operator.lower())
        final_sev = severity if required else "warning"
        init_data: dict[str, Any] = {
            "metric_name": final_metric,
            "operator": final_op,
            "threshold": threshold,
            "is_delta": is_delta,
            "severity": final_sev,
            "enabled": enabled,
            **data,
        }
        super().__init__(**init_data)

    @property
    def metric(self) -> str:
        return self.metric_name

    @property
    def required(self) -> bool:
        return self.severity == "critical"


class RegressionGate(EvalXBaseModel):
    """Represents a regression gate preventing regressions against baseline runs."""

    id: UUID
    name: str
    description: str | None = None
    configuration_id: UUID | None = None
    version: int = 1
    enabled: bool = True
    rules: list[GateRule] = Field(default_factory=list)
    snapshot_hash: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_gate_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "configuration_id" not in data and "evaluation_id" in data:
                data["configuration_id"] = data["evaluation_id"]
        return data

    @property
    def evaluation_id(self) -> UUID | None:
        return self.configuration_id


class RegressionGateVersion(EvalXBaseModel):
    """Historical snapshot of a regression gate."""

    id: UUID
    gate_id: UUID
    version: int
    name: str
    description: str | None = None
    configuration_id: UUID | None = None
    snapshot_hash: str | None = None
    rules: list[GateRule] = Field(default_factory=list)
    created_at: datetime


class GateEvaluationResult(EvalXBaseModel):
    """Outcome of evaluating a candidate run against a regression gate."""

    id: UUID
    gate_id: UUID
    gate_version: int
    candidate_run_id: UUID
    baseline_run_id: UUID | None = None
    passed: bool
    failure_reasons: list[str] = Field(default_factory=list)
    comparisons: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
