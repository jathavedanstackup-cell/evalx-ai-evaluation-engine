"""Base Pydantic model configuration for all SDK models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class EvalXBaseModel(BaseModel):
    """Base model that ignores unknown server fields for forward compatibility."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
