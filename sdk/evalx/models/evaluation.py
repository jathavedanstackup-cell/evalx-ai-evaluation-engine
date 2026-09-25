"""Evaluation, run, and result models for the EVALX SDK."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from evalx.models.common import EvalXBaseModel


class Evaluation(EvalXBaseModel):
    """Represents an evaluation definition."""

    id: UUID
    name: str
    description: str | None = None
    dataset_id: UUID
    model_provider: str
    model_name: str
    system_prompt: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class EvaluationRun(EvalXBaseModel):
    """Represents a single execution run of an evaluation."""

    id: UUID
    evaluation_id: UUID
    dataset_id: UUID | None = None
    dataset_version: int | None = None
    dataset_snapshot_hash: str | None = None
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    overall_score: float | None = None
    aggregate_score: float | None = None
    total_cases: int | None = None
    completed_cases: int | None = None
    passed_cases: int | None = None
    failed_cases: int | None = None
    error_message: str | None = None
    metrics_summary: dict[str, Any] | None = None
    config_id: UUID | None = None
    config_version: int | None = None
    created_at: datetime
    updated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_run_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            score = (
                data.get("overall_score")
                if data.get("overall_score") is not None
                else data.get("aggregate_score")
            )
            data["overall_score"] = score
            data["aggregate_score"] = score
            if "passed_cases" in data and "completed_cases" not in data:
                data["completed_cases"] = data["passed_cases"]
            elif "completed_cases" in data and "passed_cases" not in data:
                data["passed_cases"] = data["completed_cases"]
            if "config_id" in data and "configuration_id" not in data:
                data["configuration_id"] = data["config_id"]
            elif "configuration_id" in data and "config_id" not in data:
                data["config_id"] = data["configuration_id"]
        return data

    @property
    def configuration_id(self) -> UUID | None:
        return self.config_id

    @property
    def configuration_version(self) -> int | None:
        return self.config_version


class EvaluationResult(EvalXBaseModel):
    """Represents the evaluation outcome for a single test case."""

    id: UUID
    run_id: UUID
    case_id: UUID | None = None
    response: str | None = None
    overall_score: float | None = None
    passed: bool | None = None
    status: str | None = None
    feedback: str | None = None
    execution_time_ms: float | None = None
    error_message: str | None = None
    metrics: dict[str, Any] | None = None
    created_at: datetime | None = None

    @property
    def score(self) -> float | None:
        return self.overall_score

    @property
    def reason(self) -> str | None:
        return self.feedback

    @property
    def metric_scores(self) -> dict[str, float]:
        if not self.metrics:
            return {}
        scores: dict[str, float] = {}
        for k, v in self.metrics.items():
            if (
                isinstance(v, dict)
                and "score" in v
                and isinstance(v["score"], (int, float))
            ):
                scores[k] = float(v["score"])
            elif isinstance(v, (int, float)):
                scores[k] = float(v)
        return scores


class EvaluationRunResults(EvalXBaseModel):
    """Container holding aggregate score and individual results for a run."""

    run_id: UUID
    status: str
    total_expected_cases: int = 0
    completed_cases: int = 0
    failed_cases: int = 0
    items: list[EvaluationResult] = Field(default_factory=list)
    aggregate_score: float | None = None
    total: int = 0
    page: int = 1
    page_size: int = 20
    total_pages: int = 1

    @model_validator(mode="before")
    @classmethod
    def _normalize_results(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "results" in data and not data.get("items"):
                data["items"] = data["results"]
        return data

    @model_validator(mode="after")
    def _compute_aggregate_if_missing(self) -> EvaluationRunResults:
        if self.aggregate_score is None and self.items:
            valid_scores = [
                r.overall_score for r in self.items if r.overall_score is not None
            ]
            if valid_scores:
                self.aggregate_score = sum(valid_scores) / len(valid_scores)
        return self

    @property
    def results(self) -> list[EvaluationResult]:
        return self.items


class RunComparisonMetric(EvalXBaseModel):
    """Metric-level score comparison between baseline and candidate runs."""

    baseline_score: float | None = None
    candidate_score: float | None = None
    difference: float | None = None


class RunComparison(EvalXBaseModel):
    """Detailed score comparison between two evaluation runs."""

    baseline_run_id: UUID
    candidate_run_id: UUID
    baseline_aggregate_score: float | None = None
    candidate_aggregate_score: float | None = None
    score_difference: float | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    improved_cases: list[UUID] = Field(default_factory=list)
    regressed_cases: list[UUID] = Field(default_factory=list)
    unchanged_cases: list[UUID] = Field(default_factory=list)
