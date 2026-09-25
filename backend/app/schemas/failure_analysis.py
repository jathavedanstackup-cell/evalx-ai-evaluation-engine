from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FailureCategory(StrEnum):
    """Explicit taxonomy of evaluation failure categories."""

    METRIC_THRESHOLD_FAILURE = "metric_threshold_failure"
    EVALUATOR_ERROR = "evaluator_error"
    EVALUATOR_UNAVAILABLE = "evaluator_unavailable"
    MISSING_OUTPUT = "missing_output"
    MALFORMED_OUTPUT = "malformed_output"
    INSTRUCTION_FAILURE = "instruction_failure"
    FACTUALITY_FAILURE = "factuality_failure"
    RELEVANCE_FAILURE = "relevance_failure"
    FAITHFULNESS_FAILURE = "faithfulness_failure"
    HALLUCINATION_FAILURE = "hallucination_failure"
    CONSISTENCY_FAILURE = "consistency_failure"
    REGRESSION_FAILURE = "regression_failure"


class MetricThresholdFailureDetail(BaseModel):
    """Details for a specific metric that failed its threshold."""

    model_config = ConfigDict(extra="forbid")

    metric_name: str
    score: float | None = None
    threshold: float | None = None
    failure_category: FailureCategory
    reasoning: str | None = None


class CaseFailureAnalysis(BaseModel):
    """Structured analytical diagnostic for an individual failed test case."""

    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    overall_case_score: float | None = None
    passed: bool
    failed_metrics: list[str] = Field(default_factory=list)
    failure_categories: list[FailureCategory] = Field(default_factory=list)
    metric_scores: dict[str, float | None] = Field(default_factory=dict)
    metric_statuses: dict[str, str] = Field(default_factory=dict)
    threshold_failures: list[MetricThresholdFailureDetail] = Field(default_factory=list)
    execution_status: str
    comparison_delta: float | None = None


class MetricDistribution(BaseModel):
    """Deterministic distribution summary of evaluation outcomes per metric."""

    model_config = ConfigDict(extra="forbid")

    metric_name: str
    successful_evaluations: int
    threshold_failed: int
    error: int
    unavailable: int
    skipped: int
    failure_percentage: float | None = None
    average_score: float | None = None


class CategoryDistribution(BaseModel):
    """Prevalence distribution of a specific failure category across a run."""

    model_config = ConfigDict(extra="forbid")

    category: FailureCategory
    count: int
    percentage_of_cases: float | None = None


class MetricFailureSummary(BaseModel):
    """Summary of a failing metric for ranking most-affected metrics."""

    model_config = ConfigDict(extra="forbid")

    metric_name: str
    failure_count: int
    failure_rate: float


class CaseFailureSummary(BaseModel):
    """Summary of a failing case for ranking most-affected cases."""

    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    failed_metric_count: int
    failed_metrics: list[str]
    overall_score: float | None = None


class BaselineRegressionInsights(BaseModel):
    """Comparative insights contrasting target run failures against a baseline."""

    model_config = ConfigDict(extra="forbid")

    baseline_run_id: UUID
    newly_failing_cases: list[UUID] = Field(default_factory=list)
    newly_passing_cases: list[UUID] = Field(default_factory=list)
    unchanged_failures: list[UUID] = Field(default_factory=list)
    unchanged_passes: list[UUID] = Field(default_factory=list)
    metric_deltas: dict[str, float | None] = Field(default_factory=dict)
    failure_category_deltas: dict[str, int] = Field(default_factory=dict)
    regression_rate: float = 0.0
    recovery_rate: float = 0.0


class RunInsights(BaseModel):
    """High-level statistical and category aggregates for an evaluation run."""

    model_config = ConfigDict(extra="forbid")

    total_cases: int
    passed_cases: int
    failed_cases: int
    pass_rate: float
    failure_rate: float
    metric_averages: dict[str, float] = Field(default_factory=dict)
    metric_failure_counts: dict[str, int] = Field(default_factory=dict)
    metric_threshold_failure_rates: dict[str, float] = Field(default_factory=dict)
    unavailable_counts: dict[str, int] = Field(default_factory=dict)
    error_counts: dict[str, int] = Field(default_factory=dict)
    failure_category_counts: dict[str, int] = Field(default_factory=dict)
    most_affected_metrics: list[MetricFailureSummary] = Field(default_factory=list)
    most_affected_cases: list[CaseFailureSummary] = Field(default_factory=list)


class RunAnalysisResponse(BaseModel):
    """Full analytical insights and failure breakdown response for a run."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    run_id: UUID
    baseline_run_id: UUID | None = None
    summary: str
    run_insights: RunInsights
    metric_distributions: list[MetricDistribution]
    category_distributions: list[CategoryDistribution]
    failed_cases: list[CaseFailureAnalysis]
    baseline_insights: BaselineRegressionInsights | None = None
    snapshot_hash: str
    correlation_id: str | None = None
    created_at: datetime
