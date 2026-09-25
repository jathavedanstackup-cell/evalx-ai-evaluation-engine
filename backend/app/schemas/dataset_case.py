import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.observability.sanitization import validate_execution_metadata

MAX_CASE_INPUT_CHARS: int = 50_000
MAX_CASE_OUTPUT_CHARS: int = 50_000
MAX_CONTEXT_PASSAGES: int = 50
MAX_CONTEXT_PASSAGE_CHARS: int = 20_000
MAX_CONTEXT_TOTAL_BYTES: int = 1_000_000
MAX_CONTEXT_DICT_BYTES: int = 100_000


def validate_case_context(v: Any) -> Any:
    """Validates bounded context passages or structured documents."""
    if v is None:
        return v
    if isinstance(v, list):
        if len(v) > MAX_CONTEXT_PASSAGES:
            raise ValueError(
                f"Context exceeds maximum of {MAX_CONTEXT_PASSAGES} passages"
            )
        total_bytes = 0
        for i, passage in enumerate(v):
            if not isinstance(passage, str):
                raise ValueError(f"Context passage at index {i} must be a string")
            if len(passage) > MAX_CONTEXT_PASSAGE_CHARS:
                raise ValueError(
                    f"Context passage at index {i} exceeds maximum of "
                    f"{MAX_CONTEXT_PASSAGE_CHARS} characters"
                )
            total_bytes += len(passage.encode("utf-8"))
        if total_bytes > MAX_CONTEXT_TOTAL_BYTES:
            raise ValueError(
                f"Total context size ({total_bytes} bytes) exceeds maximum "
                f"limit of {MAX_CONTEXT_TOTAL_BYTES} bytes"
            )
    elif isinstance(v, dict):
        try:
            serialized = json.dumps(v, default=str)
        except Exception as exc:
            raise ValueError(
                f"Context dictionary is not JSON-serializable: {exc}"
            ) from exc
        if len(serialized.encode("utf-8")) > MAX_CONTEXT_DICT_BYTES:
            raise ValueError(
                f"Context dictionary exceeds maximum limit of "
                f"{MAX_CONTEXT_DICT_BYTES} bytes"
            )
    else:
        raise ValueError("Context must be a list of strings or a dictionary")
    return v


class DatasetCaseBase(BaseModel):
    input: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CASE_INPUT_CHARS,
        description="The prompt, question, or input query for the evaluation case",
    )
    expected_output: str | None = Field(
        default=None,
        max_length=MAX_CASE_OUTPUT_CHARS,
        description="The ideal or ground truth expected output",
    )
    context: list[str] | dict[str, Any] | None = Field(
        default=None,
        description="Retrieved context passages or structured document",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Arbitrary case metadata tags (difficulty, category, source, etc.)",
    )

    @field_validator("input")
    @classmethod
    def validate_input_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Input cannot be empty or whitespace only")
        return stripped

    @field_validator("context")
    @classmethod
    def validate_context_bounds(cls, v: Any) -> Any:
        return validate_case_context(v)

    @field_validator("metadata")
    @classmethod
    def validate_metadata_bounds(
        cls, v: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if v is not None:
            return validate_execution_metadata(v)
        return v


class DatasetCaseCreate(DatasetCaseBase):
    pass


class DatasetCaseUpdate(BaseModel):
    input: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_CASE_INPUT_CHARS,
        description="Updated prompt or input query",
    )
    expected_output: str | None = Field(
        default=None,
        max_length=MAX_CASE_OUTPUT_CHARS,
        description="Updated ground truth output",
    )
    context: list[str] | dict[str, Any] | None = Field(
        default=None,
        description="Updated context passages or document",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Updated case metadata",
    )

    @field_validator("input")
    @classmethod
    def validate_input_not_blank(cls, v: str | None) -> str | None:
        if v is not None:
            stripped = v.strip()
            if not stripped:
                raise ValueError("Input cannot be empty or whitespace only")
            return stripped
        return v

    @field_validator("context")
    @classmethod
    def validate_context_bounds(cls, v: Any) -> Any:
        return validate_case_context(v)

    @field_validator("metadata")
    @classmethod
    def validate_metadata_bounds(
        cls, v: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if v is not None:
            return validate_execution_metadata(v)
        return v


class DatasetCaseResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    input: str
    expected_output: str | None
    context: list[str] | dict[str, Any] | None
    metadata: dict[str, Any] | None = Field(
        default=None,
        validation_alias="metadata_",
    )
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class DatasetCaseBulkCreateRequest(BaseModel):
    cases: list[DatasetCaseCreate] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="List of dataset cases to ingest (bounded 1-1000 items)",
    )


class DatasetCaseBulkCreateResponse(BaseModel):
    dataset_id: UUID
    inserted_count: int
    cases: list[DatasetCaseResponse]
