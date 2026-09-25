from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GateOperator(StrEnum):
    GTE = "gte"
    GT = "gt"
    LTE = "lte"
    LT = "lt"
    EQ = "eq"


class GateSeverity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"


class GateOverallStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class RuleEvaluationStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    SKIPPED = "skipped"


MAX_GATE_NAME_LENGTH: int = 255
MAX_GATE_DESC_LENGTH: int = 2000
MAX_RULES_PER_GATE: int = 50


class GateRuleInput(BaseModel):
    """Rule definition within a regression gate."""

    model_config = ConfigDict(extra="forbid")

    metric_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Metric name to evaluate (e.g. 'factuality', 'overall_score')",
    )
    operator: GateOperator = Field(
        ...,
        description="Comparison operator: gte, gt, lte, lt, eq",
    )
    threshold: float = Field(
        ...,
        description="Threshold score or relative delta value",
    )
    is_delta: bool = Field(
        default=False,
        description="If True, compares delta (target - baseline) against threshold",
    )
    severity: GateSeverity = Field(
        default=GateSeverity.CRITICAL,
        description="Rule severity (critical rules determine pass/fail)",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this rule is active during evaluation",
    )

    @field_validator("metric_name")
    @classmethod
    def validate_metric_name(cls, v: str) -> str:
        stripped = v.strip().lower()
        if not stripped:
            raise ValueError("metric_name cannot be empty or whitespace only")
        return stripped


class GateRuleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    metric_name: str
    operator: GateOperator
    threshold: float
    is_delta: bool = False
    severity: GateSeverity = GateSeverity.CRITICAL
    enabled: bool = True


class GateRuleResult(BaseModel):
    """Individual rule evaluation outcome."""

    model_config = ConfigDict(extra="forbid")

    metric_name: str
    operator: GateOperator
    threshold: float
    is_delta: bool
    severity: GateSeverity
    target_value: float | None = None
    baseline_value: float | None = None
    delta: float | None = None
    status: RuleEvaluationStatus
    reason: str | None = None


def _validate_rules_list(rules: list[GateRuleInput]) -> list[GateRuleInput]:
    if not rules:
        raise ValueError("Regression gate must contain at least one rule")
    if not any(r.enabled for r in rules):
        raise ValueError("Regression gate must contain at least one enabled rule")

    seen: set[tuple[str, str, bool]] = set()
    for r in rules:
        key = (r.metric_name, r.operator.value, r.is_delta)
        if key in seen:
            raise ValueError(
                f"Duplicate rule detected for metric '{r.metric_name}' "
                f"with operator '{r.operator.value}' (is_delta={r.is_delta})"
            )
        seen.add(key)
    return rules


class RegressionGateCreate(BaseModel):
    """Payload to create a new regression gate."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        min_length=1,
        max_length=MAX_GATE_NAME_LENGTH,
        description="Unique name for the regression gate",
    )
    description: str | None = Field(
        default=None,
        max_length=MAX_GATE_DESC_LENGTH,
        description="Optional human-readable description",
    )
    configuration_id: UUID | None = Field(
        default=None,
        description="Optional associated evaluation configuration preset ID",
    )
    rules: list[GateRuleInput] = Field(
        ...,
        min_length=1,
        max_length=MAX_RULES_PER_GATE,
        description="List of gate rules to evaluate",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Gate name cannot be empty or whitespace only")
        return stripped

    @field_validator("rules")
    @classmethod
    def validate_rules(cls, v: list[GateRuleInput]) -> list[GateRuleInput]:
        return _validate_rules_list(v)


class RegressionGateUpdate(BaseModel):
    """Payload to update an existing regression gate (creates a new version)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_GATE_NAME_LENGTH,
        description="Updated name",
    )
    description: str | None = Field(
        default=None,
        max_length=MAX_GATE_DESC_LENGTH,
        description="Updated description",
    )
    configuration_id: UUID | None = Field(
        default=None,
        description="Updated configuration ID association",
    )
    enabled: bool | None = Field(
        default=None,
        description="Whether the gate is enabled",
    )
    rules: list[GateRuleInput] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_RULES_PER_GATE,
        description="Updated list of rules",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            raise ValueError("Gate name cannot be empty or whitespace only")
        return stripped

    @field_validator("rules")
    @classmethod
    def validate_rules(
        cls, v: list[GateRuleInput] | None
    ) -> list[GateRuleInput] | None:
        if v is None:
            return None
        return _validate_rules_list(v)

    @model_validator(mode="after")
    def validate_has_updates(self) -> Self:
        if (
            self.name is None
            and self.description is None
            and self.configuration_id is None
            and self.enabled is None
            and self.rules is None
        ):
            raise ValueError("At least one field must be provided for update")
        return self


class RegressionGateResponse(BaseModel):
    """Response representing an active regression gate."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    owner_user_id: UUID | None = None
    name: str
    description: str | None = None
    configuration_id: UUID | None = None
    version: int
    enabled: bool
    rules: list[GateRuleResponse]
    snapshot_hash: str
    created_at: datetime
    updated_at: datetime


class RegressionGateVersionResponse(BaseModel):
    """Response representing a historical version of a regression gate."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    gate_id: UUID
    version: int
    name: str
    description: str | None = None
    configuration_id: UUID | None = None
    rules: list[GateRuleResponse]
    snapshot_hash: str
    created_at: datetime


class GateEvaluateRequest(BaseModel):
    """Request payload to evaluate a regression gate against a run."""

    model_config = ConfigDict(extra="forbid")

    target_run_id: UUID = Field(
        ...,
        description="Target evaluation run ID to evaluate against the gate",
    )
    baseline_run_id: UUID | None = Field(
        default=None,
        description="Optional baseline evaluation run ID for delta comparisons",
    )


class GateEvaluateResponse(BaseModel):
    """Result of evaluating a regression gate."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    gate_id: UUID | None = None
    gate_version: int
    target_run_id: UUID
    baseline_run_id: UUID | None = None
    overall_status: GateOverallStatus
    rule_results: list[GateRuleResult]
    summary: str | None = None
    snapshot_hash: str
    correlation_id: str | None = None
    evaluated_at: datetime
