from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_EXPERIMENT_NAME_LENGTH: int = 255
MAX_EXPERIMENT_DESC_LENGTH: int = 2000


class ExperimentCreate(BaseModel):
    """Payload to create an evaluation experiment grouping comparable runs."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        min_length=1,
        max_length=MAX_EXPERIMENT_NAME_LENGTH,
        description="Experiment name",
    )
    description: str | None = Field(
        default=None,
        max_length=MAX_EXPERIMENT_DESC_LENGTH,
        description="Optional experiment description",
    )
    configuration_id: UUID | None = Field(
        default=None,
        description="Optional evaluation configuration preset ID",
    )
    baseline_run_id: UUID | None = Field(
        default=None,
        description="Optional baseline evaluation run ID",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Experiment name cannot be empty or whitespace only")
        return stripped


class ExperimentUpdate(BaseModel):
    """Payload to update an experiment."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_EXPERIMENT_NAME_LENGTH,
        description="Updated name",
    )
    description: str | None = Field(
        default=None,
        max_length=MAX_EXPERIMENT_DESC_LENGTH,
        description="Updated description",
    )
    configuration_id: UUID | None = Field(
        default=None,
        description="Updated configuration ID association",
    )
    baseline_run_id: UUID | None = Field(
        default=None,
        description="Updated baseline run ID",
    )
    status: str | None = Field(
        default=None,
        max_length=50,
        description="Updated status (active, completed, archived)",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            raise ValueError("Experiment name cannot be empty or whitespace only")
        return stripped

    @model_validator(mode="after")
    def validate_has_updates(self) -> Self:
        if (
            self.name is None
            and self.description is None
            and self.configuration_id is None
            and self.baseline_run_id is None
            and self.status is None
        ):
            raise ValueError("At least one field must be provided for update")
        return self


class ExperimentResponse(BaseModel):
    """Response representing an experiment."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    owner_user_id: UUID | None = None
    name: str
    description: str | None = None
    configuration_id: UUID | None = None
    baseline_run_id: UUID | None = None
    status: str
    created_at: datetime
    updated_at: datetime
