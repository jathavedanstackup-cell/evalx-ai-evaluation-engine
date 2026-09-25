from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DatasetBase(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Dataset name (1-255 characters)",
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional dataset description (max 2000 characters)",
    )

    @field_validator("name")
    @classmethod
    def validate_name_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Dataset name cannot be empty or whitespace only")
        return stripped


class DatasetCreate(DatasetBase):
    pass


class DatasetUpdate(BaseModel):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated dataset name",
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="Updated dataset description (max 2000 characters)",
    )

    @field_validator("name")
    @classmethod
    def validate_name_not_blank(cls, v: str | None) -> str | None:
        if v is not None:
            stripped = v.strip()
            if not stripped:
                raise ValueError("Dataset name cannot be empty or whitespace only")
            return stripped
        return v


class DatasetResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    version: int
    owner_user_id: UUID | None = None
    case_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
