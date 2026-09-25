from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.evaluation.types import ModelInfo


class JudgeRequest(BaseModel):
    """Input payload submitted to an LLM Judge for evaluation."""

    prompt: str = Field(..., min_length=1, description="Original user prompt or query")
    response: str = Field(
        ..., min_length=1, description="Target model output or response"
    )
    candidate_responses: list[str] | None = Field(
        default=None,
        description="Multiple candidate AI responses to evaluate for consistency",
    )
    reference: str | None = Field(
        default=None,
        description="Ground truth or reference target response when available",
    )
    context: list[str] | dict[str, Any] | None = Field(
        default=None,
        description="Retrieved context passages or reference documents",
    )
    rubric: str = Field(
        ...,
        min_length=1,
        description="Evaluation rubric, criteria, or task description",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary evaluation case metadata",
    )
    model_info: ModelInfo | None = Field(
        default=None,
        description="Metadata of the evaluated model when available",
    )


class ConsistencyJudgeRequest(BaseModel):
    """Structured request submitted to an LLM Judge for consistency evaluation."""

    prompt: str = Field(..., min_length=1, description="Original user prompt or query")
    candidate_responses: list[str] = Field(
        ...,
        min_length=2,
        description="Multiple candidate AI responses to evaluate for consistency",
    )
    rubric: str = Field(
        ...,
        min_length=1,
        description="Evaluation rubric, criteria, or consistency task description",
    )
    reference: str | None = Field(
        default=None,
        description="Optional ground truth reference target response",
    )
    context: list[str] | dict[str, Any] | None = Field(
        default=None,
        description="Retrieved context passages or reference documents",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary evaluation case metadata",
    )
    model_info: ModelInfo | None = Field(
        default=None,
        description="Metadata of the evaluated model when available",
    )

    def to_judge_request(self) -> JudgeRequest:
        """Convert to standardized JudgeRequest for judge execution."""
        return JudgeRequest(
            prompt=self.prompt,
            response=self.candidate_responses[0],
            candidate_responses=self.candidate_responses,
            reference=self.reference,
            context=self.context,
            rubric=self.rubric,
            metadata=self.metadata,
            model_info=self.model_info,
        )


class JudgeResponse(BaseModel):
    """Standardized structured output produced by an LLM Judge."""

    score: float = Field(
        ..., ge=0.0, le=1.0, description="Normalized score in range [0.0, 1.0]"
    )
    passed: bool | None = Field(
        default=None,
        description="True if score >= threshold; False otherwise; None if unset",
    )
    rationale: str = Field(
        default="",
        description="Detailed reasoning and justification for the score",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Extracted quotes or evidence supporting judgment",
    )
    claims: list[str] = Field(
        default_factory=list,
        description="Extracted claims analyzed during evaluation",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Judge model metadata, tokens, latency, etc.",
    )
    raw_response: str | None = Field(
        default=None,
        description="Raw unparsed model response string",
    )


@runtime_checkable
class BaseLLMJudge(Protocol):
    """Protocol defining the interface for LLM judges.

    Decouples evaluator algorithms from concrete LLM inference providers.
    """

    @property
    def model_name(self) -> str:
        """Name or identifier of the judge model."""
        ...

    async def judge(self, request: JudgeRequest) -> JudgeResponse:
        """Execute judgment on a JudgeRequest and return a structured JudgeResponse."""
        ...
