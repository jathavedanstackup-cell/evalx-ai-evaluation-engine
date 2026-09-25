"""Dataset and test case models for the EVALX SDK."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from evalx.models.common import EvalXBaseModel


class Dataset(EvalXBaseModel):
    """Represents an evaluation dataset."""

    id: UUID
    name: str
    description: str | None = None
    case_count: int = 0
    version: int = 1
    created_at: datetime
    updated_at: datetime | None = None


class DatasetCase(EvalXBaseModel):
    """Represents an individual test case within a dataset."""

    id: UUID
    dataset_id: UUID
    input: str
    expected_output: str | None = None
    context: Any | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime | None = None


class DatasetCaseCreate(EvalXBaseModel):
    """Request model for creating a single dataset case."""

    input: str
    expected_output: str | None = None
    context: Any | None = None
    metadata: dict[str, Any] | None = None


class DatasetCaseBulkCreateResponse(EvalXBaseModel):
    """Response returned upon bulk ingestion of dataset cases."""

    dataset_id: UUID
    inserted_count: int = 0
    created_count: int = 0
    cases: list[DatasetCase] = Field(default_factory=list)
    case_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_counts(cls, data: Any) -> Any:
        if isinstance(data, dict):
            cnt = (
                data.get("created_count")
                if data.get("created_count") is not None
                else data.get("inserted_count", 0)
            )
            data["created_count"] = cnt
            data["inserted_count"] = cnt
            if not data.get("case_ids") and data.get("cases"):
                data["case_ids"] = [
                    c.get("id") if isinstance(c, dict) else getattr(c, "id", None)
                    for c in data["cases"]
                    if c is not None
                ]
        return data


class ValidationIssue(EvalXBaseModel):
    """Describes an issue detected during dataset validation."""

    severity: str
    code: str
    message: str
    case_id: UUID | None = None
    field: str | None = None


class DatasetValidationResult(EvalXBaseModel):
    """Report returned from dataset validation."""

    is_valid: bool
    total_cases: int
    issues: list[ValidationIssue] = Field(default_factory=list)
