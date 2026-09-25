"""Failure analysis and clustering models for the EVALX SDK."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from evalx.models.common import EvalXBaseModel


class FailureCluster(EvalXBaseModel):
    """Cluster of similar evaluation failures identified during analysis."""

    cluster_id: str
    name: str
    description: str
    case_count: int
    case_ids: list[UUID] = Field(default_factory=list)
    representative_failure: str | None = None


class FailureInsight(EvalXBaseModel):
    """Actionable diagnostic insight generated from run failure patterns."""

    category: str
    title: str
    description: str
    severity: str
    affected_case_count: int


class RunAnalysis(EvalXBaseModel):
    """Comprehensive failure analysis report for an evaluation run."""

    run_id: UUID
    summary: str = ""
    run_insights: dict[str, Any] = Field(default_factory=dict)
    metric_distributions: list[dict[str, Any]] = Field(default_factory=list)
    category_distributions: list[dict[str, Any]] = Field(default_factory=list)
    failed_cases: list[Any] = Field(default_factory=list)
    clusters: list[FailureCluster] = Field(default_factory=list)
    insights: list[FailureInsight] = Field(default_factory=list)
    baseline_run_id: UUID | None = None
    baseline_insights: dict[str, Any] | None = None
    snapshot_hash: str | None = None
    correlation_id: str | None = None
    created_at: datetime | None = None

    @property
    def total_cases(self) -> int:
        return int(self.run_insights.get("total_cases", len(self.failed_cases)))

    @property
    def failed_cases_count(self) -> int:
        return int(self.run_insights.get("failed_cases", len(self.failed_cases)))

    @property
    def failure_rate(self) -> float:
        return float(self.run_insights.get("failure_rate", 0.0))
