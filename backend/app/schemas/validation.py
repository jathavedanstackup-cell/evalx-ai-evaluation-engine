from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ValidationIssue(BaseModel):
    severity: Literal["error", "warning"] = Field(
        ...,
        description="Severity level of the validation issue",
    )
    case_index: int | None = Field(
        default=None,
        description="0-indexed position of the case with the issue, if applicable",
    )
    field: str | None = Field(
        default=None,
        description="The field name associated with the issue",
    )
    message: str = Field(
        ...,
        description="Human-readable description of the validation issue",
    )


class DatasetValidationResult(BaseModel):
    dataset_id: UUID
    is_valid: bool = Field(
        ...,
        description="True if there are zero error-level issues",
    )
    total_cases: int = Field(
        ...,
        ge=0,
        description="Total number of cases evaluated during validation",
    )
    error_count: int = Field(
        ...,
        ge=0,
        description="Number of critical validation errors",
    )
    warning_count: int = Field(
        ...,
        ge=0,
        description="Number of non-blocking warnings",
    )
    issues: list[ValidationIssue] = Field(
        default_factory=list,
        description="List of detected validation errors and warnings",
    )
    summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Detailed summary statistics for the dataset",
    )
