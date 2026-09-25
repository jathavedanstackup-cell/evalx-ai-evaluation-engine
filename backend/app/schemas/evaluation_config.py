from datetime import datetime
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.evaluation.enums import EvaluatorBackend, EvaluatorType

MAX_CONFIG_NAME_LENGTH: int = 255
MAX_CONFIG_DESC_LENGTH: int = 2000
MAX_EVALUATORS_PER_CONFIG: int = 50


class EvaluatorDefinitionInput(BaseModel):
    """Definition of a single evaluator within a reusable evaluation configuration."""

    model_config = ConfigDict(extra="forbid")

    evaluator_type: str = Field(
        ...,
        description=(
            "Evaluator category: factuality, relevance, faithfulness, "
            "hallucination, consistency, instruction_following"
        ),
    )
    backend: str = Field(
        default=EvaluatorBackend.LLM_JUDGE.value,
        description="Backend engine: native, llm_judge, deepeval",
    )
    threshold: float | None = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Pass/fail score threshold between 0.0 and 1.0",
    )
    weight: float = Field(
        default=1.0,
        gt=0.0,
        le=100.0,
        description="Positive relative weight for weighted case score aggregation",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this evaluator is actively executed in runs",
    )
    name: str | None = Field(
        default=None,
        max_length=100,
        description="Optional custom identifier for the evaluator",
    )
    configuration: dict[str, Any] | None = Field(
        default=None,
        description="Evaluator execution parameters (rubric, temperature, etc.)",
    )

    @field_validator("evaluator_type", "backend")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        stripped = v.strip().lower()
        if not stripped:
            raise ValueError("Field cannot be empty or whitespace only")
        return stripped

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is not None:
            stripped = v.strip()
            if not stripped:
                raise ValueError("Evaluator name cannot be empty or whitespace only")
            return stripped
        return None

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
            raise ValueError("Evaluator configuration exceeds maximum 50 keys")
        import json

        try:
            serialized = json.dumps(v)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Evaluator configuration must be JSON-serializable: {exc}"
            ) from exc
        if len(serialized.encode("utf-8")) > 65536:
            raise ValueError("Evaluator configuration exceeds maximum size of 64 KB")
        return v

    @model_validator(mode="after")
    def validate_compatibility(self) -> Self:
        # 1. Validate evaluator_type is valid enum
        try:
            norm_type = EvaluatorType(self.evaluator_type)
        except ValueError as err:
            valid_types = [t.value for t in EvaluatorType]
            raise ValueError(
                f"Unsupported evaluator type '{self.evaluator_type}'. "
                f"Supported types: {valid_types}"
            ) from err

        # 2. Validate backend is valid enum
        try:
            norm_backend = EvaluatorBackend(self.backend)
        except ValueError as err:
            valid_backends = [b.value for b in EvaluatorBackend]
            raise ValueError(
                f"Unsupported evaluator backend '{self.backend}'. "
                f"Supported backends: {valid_backends}"
            ) from err

        # 3. Validate backend-evaluator compatibility
        if norm_backend == EvaluatorBackend.DEEPEVAL:
            supported_deepeval = {
                EvaluatorType.RELEVANCE,
                EvaluatorType.FAITHFULNESS,
                EvaluatorType.HALLUCINATION,
            }
            if norm_type not in supported_deepeval:
                raise ValueError(
                    f"DeepEval backend does not support evaluator type "
                    f"'{norm_type.value}'. Supported types: "
                    f"{[t.value for t in supported_deepeval]}"
                )

        elif norm_backend == EvaluatorBackend.NATIVE:
            if norm_type != EvaluatorType.INSTRUCTION_FOLLOWING:
                raise ValueError(
                    f"Native backend does not support evaluator type "
                    f"'{norm_type.value}'. Only "
                    f"'{EvaluatorType.INSTRUCTION_FOLLOWING.value}' is supported."
                )

        elif norm_backend == EvaluatorBackend.LLM_JUDGE:
            supported_llm = {
                EvaluatorType.FACTUALITY,
                EvaluatorType.RELEVANCE,
                EvaluatorType.FAITHFULNESS,
                EvaluatorType.HALLUCINATION,
                EvaluatorType.CONSISTENCY,
            }
            if norm_type not in supported_llm:
                raise ValueError(
                    f"LLM Judge backend does not support evaluator type "
                    f"'{norm_type.value}'. Supported types: "
                    f"{[t.value for t in supported_llm]}"
                )

        return self


def _validate_evaluator_definitions(
    evaluators: list[EvaluatorDefinitionInput],
) -> list[EvaluatorDefinitionInput]:
    if not evaluators:
        raise ValueError("Configuration must contain at least one evaluator")

    # At least one must be enabled
    if not any(e.enabled for e in evaluators):
        raise ValueError("Configuration must contain at least one enabled evaluator")

    # Reject duplicate definitions where ambiguous
    seen_identifiers: set[str] = set()
    for e in evaluators:
        effective_name = e.name or e.evaluator_type
        identifier = f"{e.evaluator_type}:{effective_name}"
        if identifier in seen_identifiers:
            raise ValueError(
                f"Duplicate evaluator definition detected for '{effective_name}'. "
                "Specify unique names to distinguish multiple evaluators "
                "of the same type."
            )
        seen_identifiers.add(identifier)

    return evaluators


class EvaluationConfigCreate(BaseModel):
    """Payload to create a new reusable evaluation configuration."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CONFIG_NAME_LENGTH,
        description="Name of the evaluation configuration",
    )
    description: str | None = Field(
        default=None,
        max_length=MAX_CONFIG_DESC_LENGTH,
        description="Optional description of the configuration purpose",
    )
    evaluators: list[EvaluatorDefinitionInput] = Field(
        ...,
        min_length=1,
        max_length=MAX_EVALUATORS_PER_CONFIG,
        description="List of evaluator definitions in this configuration",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Configuration name cannot be empty or whitespace only")
        return stripped

    @field_validator("evaluators")
    @classmethod
    def validate_evaluators_list(
        cls, v: list[EvaluatorDefinitionInput]
    ) -> list[EvaluatorDefinitionInput]:
        return _validate_evaluator_definitions(v)


class EvaluationConfigUpdate(BaseModel):
    """Payload to update an evaluation configuration (creates a new version)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_CONFIG_NAME_LENGTH,
        description="Updated name",
    )
    description: str | None = Field(
        default=None,
        max_length=MAX_CONFIG_DESC_LENGTH,
        description="Updated description",
    )
    evaluators: list[EvaluatorDefinitionInput] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_EVALUATORS_PER_CONFIG,
        description="Updated evaluator definitions",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is not None:
            stripped = v.strip()
            if not stripped:
                raise ValueError(
                    "Configuration name cannot be empty or whitespace only"
                )
            return stripped
        return None

    @field_validator("evaluators")
    @classmethod
    def validate_evaluators_list(
        cls, v: list[EvaluatorDefinitionInput] | None
    ) -> list[EvaluatorDefinitionInput] | None:
        if v is not None:
            return _validate_evaluator_definitions(v)
        return None


class EvaluatorDefinitionResponse(BaseModel):
    """Public representation of an evaluator definition."""

    evaluator_type: str
    backend: str
    threshold: float | None = None
    weight: float = 1.0
    enabled: bool = True
    name: str | None = None
    configuration: dict[str, Any] | None = None


class EvaluationConfigResponse(BaseModel):
    """Public representation of an evaluation configuration."""

    id: UUID
    name: str
    description: str | None = None
    version: int
    owner_user_id: UUID | None = None
    evaluators: list[EvaluatorDefinitionResponse]
    snapshot_hash: str
    created_at: datetime
    updated_at: datetime


class EvaluationConfigVersionResponse(BaseModel):
    """Public representation of a specific historical configuration version."""

    id: UUID
    config_id: UUID
    version: int
    name: str
    description: str | None = None
    evaluators: list[EvaluatorDefinitionResponse]
    snapshot_hash: str
    created_at: datetime
