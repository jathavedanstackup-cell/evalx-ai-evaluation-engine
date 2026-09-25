from datetime import datetime
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_CASES_PER_RUN: int = 100
MAX_EVALUATORS_PER_RUN: int = 10
MAX_RESPONSE_CHARS: int = 50_000
MAX_TOTAL_RESPONSES_BYTES: int = 2_000_000  # 2 MB total limit
MAX_CONFIG_SIZE_BYTES: int = 65_536


class EvaluatorConfigInput(BaseModel):
    """Configuration for an evaluator to execute within a run."""

    evaluator_type: str = Field(
        ...,
        min_length=1,
        description=(
            "Evaluator category: factuality, relevance, faithfulness, "
            "hallucination, consistency, instruction_following"
        ),
    )
    backend: str = Field(
        default="llm_judge",
        description="Evaluator backend implementation: llm_judge, deepeval, native",
    )
    name: str | None = Field(
        default=None,
        description="Optional custom metric or evaluator name",
    )
    threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Explicit passing threshold score between 0.0 and 1.0",
    )
    weight: float | None = Field(
        default=None,
        gt=0.0,
        le=100.0,
        description="Evaluator weight for aggregate scoring",
    )
    configuration: dict[str, Any] | None = Field(
        default=None,
        description="Evaluator parameters (temperature, rubrics, rules, etc.)",
    )

    @field_validator("evaluator_type", "backend")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Field cannot be empty or whitespace only")
        return stripped.lower()

    @field_validator("configuration")
    @classmethod
    def validate_configuration_bounds(
        cls, v: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if v is None:
            return None
        if not isinstance(v, dict):
            raise ValueError("configuration must be a dictionary")
        if len(v) > 50:
            raise ValueError("configuration exceeds maximum of 50 keys")
        import json

        from app.observability.sanitization import _get_dict_depth

        depth = _get_dict_depth(v)
        if depth > 3:
            raise ValueError("configuration exceeds maximum nesting depth of 3")
        try:
            serialized = json.dumps(v, default=str)
        except Exception as exc:
            raise ValueError(f"configuration is not JSON-serializable: {exc}") from exc
        if len(serialized.encode("utf-8")) > MAX_CONFIG_SIZE_BYTES:
            raise ValueError(
                f"configuration exceeds maximum limit of {MAX_CONFIG_SIZE_BYTES} bytes"
            )
        return v


class CaseResponseInput(BaseModel):
    """Candidate AI response(s) to be evaluated for a specific dataset case."""

    case_id: UUID = Field(..., description="Target DatasetCase identifier")
    response: str | None = Field(
        default=None,
        max_length=MAX_RESPONSE_CHARS,
        description="Primary candidate AI response to evaluate",
    )
    candidate_responses: list[str] | None = Field(
        default=None,
        description="Multiple candidate AI responses to evaluate for consistency",
    )

    @model_validator(mode="after")
    def validate_has_response(self) -> Self:
        if not self.response and not self.candidate_responses:
            raise ValueError(
                "Either 'response' or 'candidate_responses' must be provided"
            )
        if self.response is not None:
            stripped = self.response.strip()
            if not stripped:
                raise ValueError(
                    "Candidate response cannot be empty or whitespace only"
                )
            self.response = stripped
        if self.candidate_responses is not None:
            if not self.candidate_responses:
                raise ValueError("candidate_responses cannot be an empty list")
            cleaned = []
            for r in self.candidate_responses:
                if not isinstance(r, str) or not r.strip():
                    raise ValueError(
                        "Candidate response cannot be empty or whitespace only"
                    )
                cleaned.append(r.strip())
            self.candidate_responses = cleaned
            if not self.response:
                self.response = cleaned[0]
        return self


class EvaluationRunCreate(BaseModel):
    """Payload to launch a synchronous persisted evaluation run."""

    dataset_id: UUID = Field(..., description="Target dataset UUID to evaluate")
    evaluation_id: UUID | None = Field(
        default=None,
        description="Optional existing Evaluation entity to associate this run with",
    )
    name: str | None = Field(
        default=None,
        description=(
            "Optional run name (or new evaluation name if evaluation_id omitted)"
        ),
    )

    evaluators: list[EvaluatorConfigInput] = Field(
        default_factory=list,
        max_length=MAX_EVALUATORS_PER_RUN,
        description="List of evaluators to execute against each case",
    )
    config_id: UUID | None = Field(
        default=None,
        description="Optional reusable evaluation configuration ID",
    )
    config_version: int | None = Field(
        default=None,
        description="Optional specific configuration version to run",
    )
    responses: list[CaseResponseInput] | dict[UUID, str | list[str]] | None = Field(
        default=None,
        description="Candidate AI responses mapped to case IDs",
    )
    model_provider: str = Field(
        default="custom",
        description="Descriptive metadata for the target model provider",
    )
    model_name: str = Field(
        default="default",
        description="Descriptive metadata for the target model name",
    )
    system_prompt: str | None = Field(
        default=None,
        description="Optional system prompt used during response generation",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Optional evaluation run metadata tags",
    )
    execution_metadata: dict[str, Any] | None = Field(
        default=None,
        description="Bounded operational metadata tags (max 50 keys, 16KB)",
    )

    @field_validator("execution_metadata", "metadata")
    @classmethod
    def validate_meta_bounds(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            from app.observability.sanitization import validate_execution_metadata

            return validate_execution_metadata(v)
        return v

    @field_validator("responses", mode="before")
    @classmethod
    def validate_total_responses_size(cls, v: Any) -> Any:
        if v is not None:
            total_bytes = 0
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        resp_str = item.get("response", "")
                        total_bytes += len(str(resp_str).encode("utf-8"))
                        if resp_str:
                            total_bytes += len(str(resp_str).encode("utf-8"))
                        cand_list = item.get("candidate_responses")
                        if isinstance(cand_list, list):
                            for cr in cand_list:
                                total_bytes += len(str(cr).encode("utf-8"))
                    elif hasattr(item, "response"):
                        total_bytes += len(str(item.response).encode("utf-8"))
                        if item.response:
                            total_bytes += len(str(item.response).encode("utf-8"))
                        cand_list = getattr(item, "candidate_responses", None)
                        if isinstance(cand_list, list):
                            for cr in cand_list:
                                total_bytes += len(str(cr).encode("utf-8"))
            elif isinstance(v, dict):
                for resp in v.values():
                    total_bytes += len(str(resp).encode("utf-8"))
                    if isinstance(resp, list):
                        for cr in resp:
                            total_bytes += len(str(cr).encode("utf-8"))
                    else:
                        total_bytes += len(str(resp).encode("utf-8"))
            if total_bytes > MAX_TOTAL_RESPONSES_BYTES:
                raise ValueError(
                    f"Total candidate_responses size ({total_bytes} bytes) exceeds "
                    f"maximum limit of {MAX_TOTAL_RESPONSES_BYTES} bytes"
                )
        return v

    @model_validator(mode="after")
    def validate_evaluators_or_config(self) -> Self:
        if self.evaluation_id is None:
            if not self.evaluators and self.config_id is None:
                raise ValueError(
                    "Either 'evaluators' or 'config_id' must be provided "
                    "when 'evaluation_id' is omitted."
                )
        return self


class EvaluationResultResponse(BaseModel):
    """Persisted case-level evaluation result."""

    id: UUID
    run_id: UUID
    case_id: UUID
    response: str | None = None
    overall_score: float | None = None
    passed: bool | None = None
    status: str | None = None
    factuality_score: float | None = None
    relevance_score: float | None = None
    faithfulness_score: float | None = None
    instruction_score: float | None = None
    consistency_score: float | None = None
    hallucination_score: float | None = None
    feedback: str | None = None
    execution_time_ms: float | None = None
    error_message: str | None = None
    metrics: dict[str, Any] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EvaluationRunResponse(BaseModel):
    """Summary of a persisted evaluation run."""

    id: UUID
    evaluation_id: UUID
    dataset_id: UUID
    dataset_version: int
    dataset_snapshot_hash: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    correlation_id: str | None = None
    overall_score: float | None = None
    total_cases: int
    completed_cases: int
    failed_cases: int
    error_message: str | None = None
    metrics_summary: dict[str, Any] | None = None
    execution_metadata: dict[str, Any] | None = None
    config_id: UUID | None = None
    config_version: int | None = None
    config_snapshot_hash: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EvaluationRunResultsResponse(BaseModel):
    """Paginated case-level evaluation results enriched with run progress status."""

    run_id: UUID
    status: str
    total_expected_cases: int
    completed_cases: int
    failed_cases: int
    items: list[EvaluationResultResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class ReapStaleRunsResponse(BaseModel):
    """Result summary of a stale evaluation run reap maintenance operation."""

    reaped_count: int = Field(..., description="Number of stale evaluation runs reaped")
    reaped_run_ids: list[UUID] = Field(..., description="UUIDs of reaped runs")
