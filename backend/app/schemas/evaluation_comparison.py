from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MetricComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_name: str
    base_score: float | None = None
    target_score: float | None = None
    delta: float | None = None
    improved: bool = False
    regressed: bool = False


class CaseComparisonCategory(StrEnum):
    REGRESSION = "regression"
    IMPROVEMENT = "improvement"
    UNCHANGED = "unchanged"
    ADDED = "added"
    REMOVED = "removed"


class CaseComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    category: CaseComparisonCategory
    base_score: float | None = None
    target_score: float | None = None
    delta: float | None = None
    base_passed: bool | None = None
    target_passed: bool | None = None
    metrics: dict[str, MetricComparison] = Field(default_factory=dict)


class RunComparisonSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_run_id: UUID
    target_run_id: UUID
    base_overall_score: float | None = None
    target_overall_score: float | None = None
    overall_score_delta: float | None = None
    base_dataset_version: int
    target_dataset_version: int
    base_snapshot_hash: str
    target_snapshot_hash: str
    total_cases_compared: int
    regressions_count: int
    improvements_count: int
    unchanged_count: int
    added_cases_count: int
    removed_cases_count: int
    metric_comparisons: dict[str, MetricComparison] = Field(default_factory=dict)


class RunComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: RunComparisonSummary
    case_comparisons: list[CaseComparison] = Field(default_factory=list)
