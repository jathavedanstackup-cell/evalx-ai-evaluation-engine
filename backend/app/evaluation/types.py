from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.evaluation.enums import EvaluatorType, MetricStatus


class ModelInfo(BaseModel):
    """Metadata regarding the target AI model whose response is evaluated."""

    provider: str = Field(
        ..., min_length=1, description="Model provider (e.g. openai, anthropic)"
    )
    model_name: str = Field(
        ...,
        min_length=1,
        description="Model name or identifier (e.g. gpt-4o, claude-3-5-sonnet)",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional generation parameters (temperature, max_tokens, etc.)",
    )

    model_config = ConfigDict(frozen=True)


class EvaluationContext(BaseModel):
    """Context bundle with original query, ground truth, and retrieved data."""

    input: str = Field(
        ...,
        min_length=1,
        description="The prompt or query that was presented to the target model",
    )
    expected_output: str | None = Field(
        default=None,
        description="Ground truth or reference target response when available",
    )
    context: list[str] | dict[str, Any] | None = Field(
        default=None,
        description="Retrieved context passages (RAG) or reference document",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Arbitrary case tags and metadata",
    )
    model_info: ModelInfo | None = Field(
        default=None,
        description="Model provider and configuration info when available",
    )
    system_prompt: str | None = Field(
        default=None,
        description="System prompt used for generation when applicable",
    )
    candidate_responses: list[str] | None = Field(
        default=None,
        description="Multiple candidate AI responses to evaluate for consistency",
    )

    @field_validator("input")
    @classmethod
    def validate_input_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Context input cannot be empty or whitespace only")
        return stripped


class EvaluationInput(BaseModel):
    """Complete input packet required to evaluate one model response."""

    case_id: UUID | None = Field(
        default=None, description="Optional associated dataset case UUID"
    )
    response: str = Field(
        ...,
        min_length=1,
        description="The model output/response string to be evaluated",
    )
    candidate_responses: list[str] | None = Field(
        default=None,
        description="Multiple candidate AI responses to evaluate for consistency",
    )
    context: EvaluationContext = Field(
        ...,
        description="Evaluation context including prompt, context, ground truth",
    )

    @field_validator("response")
    @classmethod
    def validate_response_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Response cannot be empty or whitespace only")
        return stripped

    @property
    def input(self) -> str:
        return self.context.input

    @property
    def expected_output(self) -> str | None:
        return self.context.expected_output

    @property
    def retrieved_context(self) -> list[str] | dict[str, Any] | None:
        return self.context.context

    @property
    def metadata(self) -> dict[str, Any] | None:
        return self.context.metadata

    @property
    def model_info(self) -> ModelInfo | None:
        return self.context.model_info

    @property
    def system_prompt(self) -> str | None:
        return self.context.system_prompt

    @property
    def all_candidate_responses(self) -> list[str] | None:
        """Resolves multi-response candidate list from input, context, or metadata."""
        if self.candidate_responses is not None:
            return self.candidate_responses
        if self.context.candidate_responses is not None:
            return self.context.candidate_responses
        if self.context.metadata and isinstance(self.context.metadata, dict):
            meta_resps = self.context.metadata.get(
                "candidate_responses"
            ) or self.context.metadata.get("responses")
            if isinstance(meta_resps, list):
                return [str(r) for r in meta_resps]
        return None

    @classmethod
    def create(
        cls,
        input: str,
        response: str | None = None,
        candidate_responses: list[str] | None = None,
        case_id: UUID | None = None,
        expected_output: str | None = None,
        context: list[str] | dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        model_info: ModelInfo | None = None,
        system_prompt: str | None = None,
    ) -> Self:
        """Construct an EvaluationInput without manually creating EvaluationContext."""
        actual_response = response
        if (
            actual_response is None or not actual_response.strip()
        ) and candidate_responses:
            for cr in candidate_responses:
                if cr and cr.strip():
                    actual_response = cr
                    break
        ctx = EvaluationContext(
            input=input,
            expected_output=expected_output,
            context=context,
            metadata=metadata,
            model_info=model_info,
            system_prompt=system_prompt,
            candidate_responses=candidate_responses,
        )
        return cls(
            case_id=case_id,
            response=actual_response,  # type: ignore[arg-type]
            candidate_responses=candidate_responses,
            context=ctx,
        )


class EvaluationMetric(BaseModel):
    """Result produced by a single evaluator for an evaluation case."""

    metric_name: str = Field(
        ..., min_length=1, description="Name or identifier of the metric"
    )
    evaluator_type: EvaluatorType = Field(..., description="Category of the evaluator")
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Normalized metric score in range [0.0, 1.0]",
    )
    passed: bool | None = Field(
        default=None,
        description="True if score >= threshold; False otherwise; None if unset",
    )
    threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Explicit passing threshold in range [0.0, 1.0]",
    )
    explanation: str | None = Field(
        default=None,
        description="Reasoning or diagnostic feedback for the score",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional evaluator confidence score in range [0.0, 1.0]",
    )
    status: MetricStatus = Field(
        default=MetricStatus.SUCCESS,
        description="Execution status of the metric evaluation",
    )
    execution_time_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Evaluator execution duration in milliseconds",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Evaluator-specific diagnostic metadata",
    )


class EvaluationCaseResult(BaseModel):
    """Represents all metric results for a single DatasetCase."""

    case_id: UUID | None = Field(
        default=None, description="DatasetCase ID when available"
    )
    response: str | None = Field(default=None, description="The evaluated response")
    metrics: dict[str, EvaluationMetric] = Field(
        default_factory=dict,
        description="Dictionary mapping metric name to EvaluationMetric",
    )
    overall_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Aggregated case score in range [0.0, 1.0]",
    )
    passed: bool | None = Field(
        default=None,
        description="True if all thresholded metrics passed; False if any failed",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Non-fatal execution errors encountered during case evaluation",
    )
    execution_time_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Total evaluation duration for this case in milliseconds",
    )


class EvaluationRunResult(BaseModel):
    """Represents aggregate results across a complete EvaluationRun."""

    run_id: UUID | None = Field(
        default=None, description="EvaluationRun ID when available"
    )
    total_cases: int = Field(ge=0, description="Total cases in the evaluation run")
    completed_cases: int = Field(
        ge=0, description="Cases that completed evaluation successfully"
    )
    failed_cases: int = Field(
        ge=0, description="Cases that encountered execution errors or failed thresholds"
    )
    overall_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Deterministic overall aggregate score in range [0.0, 1.0]",
    )
    metric_averages: dict[str, float] = Field(
        default_factory=dict,
        description="Average score per metric name across all evaluated cases",
    )
    case_results: list[EvaluationCaseResult] = Field(
        default_factory=list,
        description="Granular result for each evaluated case",
    )
    execution_time_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Total duration of the evaluation run in milliseconds",
    )
