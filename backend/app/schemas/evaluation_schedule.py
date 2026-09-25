"""Pydantic schemas and deterministic scheduling utilities for Step 15.

Defines schedule models, recurrence specifications, timezone validation,
and next-run calculation algorithms adhering to EVALX quality gates.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings

_TIME_OF_DAY_REGEX = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?$")


class ScheduleType(StrEnum):
    """Supported schedule recurrence models."""

    ONE_TIME = "one_time"
    DAILY = "daily"
    WEEKLY = "weekly"
    INTERVAL = "interval"


class ScheduleStatus(StrEnum):
    """Lifecycle and operational state of a schedule."""

    PENDING = "pending"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    MISSED = "missed"
    PAUSED = "paused"


class ScheduleExecutionStatus(StrEnum):
    """Outcome status of an individual schedule trigger/execution."""

    TRIGGERED = "triggered"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    MISSED = "missed"


class ScheduleDefinition(BaseModel):
    """Deterministic recurrence and timing configuration."""

    timezone: str = Field(
        default="UTC",
        description="IANA standard timezone name (e.g. UTC, America/New_York)",
    )
    start_time: datetime | None = Field(
        default=None,
        description="Initial start time anchor (UTC or timezone-aware)",
    )
    end_time: datetime | None = Field(
        default=None,
        description="Optional expiration datetime after which schedule ceases",
    )
    time_of_day: str | None = Field(
        default=None,
        description="HH:MM or HH:MM:SS in 24-hour format for daily/weekly recurrence",
    )
    days_of_week: list[int] | None = Field(
        default=None,
        description="Days of week for weekly schedule: 0=Monday .. 6=Sunday",
    )
    interval_minutes: int | None = Field(
        default=None,
        description="Interval in minutes for interval-based recurrence",
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean:
            return "UTC"
        try:
            ZoneInfo(clean)
        except ZoneInfoNotFoundError as err:
            raise ValueError(f"Invalid IANA timezone name: '{clean}'") from err
        return clean

    @field_validator("time_of_day")
    @classmethod
    def validate_time_of_day(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip()
        if not _TIME_OF_DAY_REGEX.match(clean):
            raise ValueError(
                f"time_of_day must be 'HH:MM' or 'HH:MM:SS' (00:00-23:59), "
                f"got '{clean}'"
            )
        return clean

    @field_validator("days_of_week")
    @classmethod
    def validate_days_of_week(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return None
        if not v:
            raise ValueError("days_of_week cannot be empty when provided")
        for day in v:
            if not isinstance(day, int) or day < 0 or day > 6:
                raise ValueError(
                    f"days_of_week items must be 0 (Mon) to 6 (Sun), got {day}"
                )
        return sorted(list(set(v)))

    @field_validator("interval_minutes")
    @classmethod
    def validate_interval(cls, v: int | None) -> int | None:
        if v is None:
            return None
        min_allowed = settings.min_schedule_interval_minutes
        if v < min_allowed:
            raise ValueError(f"interval_minutes must be at least {min_allowed} minutes")
        if v > 525_600:  # 1 year in minutes
            raise ValueError("interval_minutes cannot exceed 525,600 (1 year)")
        return v

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValueError("start_time must be strictly before end_time")
        return self


def parse_time_of_day(tod_str: str | None) -> time:
    """Parses HH:MM or HH:MM:SS string into a time object."""
    if not tod_str:
        return time(0, 0, 0)
    parts = [int(p) for p in tod_str.split(":")]
    if len(parts) == 2:
        return time(parts[0], parts[1], 0)
    return time(parts[0], parts[1], parts[2])


def compute_next_run_at(
    schedule_type: ScheduleType | str,
    definition: ScheduleDefinition | dict[str, Any],
    from_time: datetime | None = None,
) -> datetime | None:
    """Deterministically computes the next execution datetime in UTC.

    Handles timezones, daylight savings, interval stepping, and expiration.
    Returns None if schedule is expired or invalid.
    """
    if isinstance(definition, dict):
        parsed_def = ScheduleDefinition(**definition)
    else:
        parsed_def = definition

    st = (
        ScheduleType(schedule_type) if isinstance(schedule_type, str) else schedule_type
    )

    tz = ZoneInfo(parsed_def.timezone)
    now_utc = from_time or datetime.now(UTC)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    # Check expiration
    if parsed_def.end_time is not None:
        end_utc = parsed_def.end_time
        if end_utc.tzinfo is None:
            end_utc = end_utc.replace(tzinfo=UTC)
        if now_utc >= end_utc:
            return None

    if st == ScheduleType.ONE_TIME:
        if not parsed_def.start_time:
            return None
        target = parsed_def.start_time
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        target_utc = target.astimezone(UTC)
        return target_utc if target_utc > now_utc else None

    if st == ScheduleType.INTERVAL:
        interval_mins = (
            parsed_def.interval_minutes or settings.min_schedule_interval_minutes
        )
        delta = timedelta(minutes=interval_mins)
        if parsed_def.start_time:
            start_utc = parsed_def.start_time
            if start_utc.tzinfo is None:
                start_utc = start_utc.replace(tzinfo=UTC)
            if now_utc < start_utc:
                candidate = start_utc
            else:
                elapsed = now_utc - start_utc
                steps = int(elapsed.total_seconds() // delta.total_seconds()) + 1
                candidate = start_utc + (steps * delta)
        else:
            candidate = now_utc + delta

        if parsed_def.end_time:
            end_utc = parsed_def.end_time
            if end_utc.tzinfo is None:
                end_utc = end_utc.replace(tzinfo=UTC)
            if candidate >= end_utc:
                return None
        return candidate

    if st == ScheduleType.DAILY:
        tod = parse_time_of_day(parsed_def.time_of_day)
        local_now = now_utc.astimezone(tz)
        candidate_local = local_now.replace(
            hour=tod.hour, minute=tod.minute, second=tod.second, microsecond=0
        )
        if candidate_local <= local_now:
            candidate_local += timedelta(days=1)

        candidate_utc = candidate_local.astimezone(UTC)
        if parsed_def.end_time:
            end_utc = parsed_def.end_time
            if end_utc.tzinfo is None:
                end_utc = end_utc.replace(tzinfo=UTC)
            if candidate_utc >= end_utc:
                return None
        return candidate_utc

    if st == ScheduleType.WEEKLY:
        tod = parse_time_of_day(parsed_def.time_of_day)
        days = parsed_def.days_of_week or [0]  # default Monday
        local_now = now_utc.astimezone(tz)

        candidates: list[datetime] = []
        for day in days:
            days_ahead = (day - local_now.weekday()) % 7
            candidate_local = local_now.replace(
                hour=tod.hour, minute=tod.minute, second=tod.second, microsecond=0
            ) + timedelta(days=days_ahead)

            if candidate_local <= local_now:
                candidate_local += timedelta(days=7)
            candidates.append(candidate_local.astimezone(UTC))

        if not candidates:
            return None

        best_utc = min(candidates)
        if parsed_def.end_time:
            end_utc = parsed_def.end_time
            if end_utc.tzinfo is None:
                end_utc = end_utc.replace(tzinfo=UTC)
            if best_utc >= end_utc:
                return None
        return best_utc

    return None


class EvaluationScheduleCreate(BaseModel):
    """Payload to define and schedule automated evaluation executions."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool = Field(default=True)
    schedule_type: ScheduleType
    schedule_definition: ScheduleDefinition
    dataset_id: UUID
    configuration_id: UUID | None = None
    configuration_version: int | None = None
    baseline_run_id: UUID | None = None
    gate_id: UUID | None = None

    @model_validator(mode="after")
    def validate_type_definition_alignment(self) -> Self:
        st = self.schedule_type
        sd = self.schedule_definition
        if st == ScheduleType.ONE_TIME and not sd.start_time:
            raise ValueError("start_time is required for ONE_TIME schedules")
        if st == ScheduleType.INTERVAL and not sd.interval_minutes:
            raise ValueError("interval_minutes is required for INTERVAL schedules")
        if st == ScheduleType.WEEKLY and not sd.days_of_week:
            raise ValueError("days_of_week is required for WEEKLY schedules")
        return self


class EvaluationScheduleUpdate(BaseModel):
    """Payload to update an existing evaluation schedule."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    schedule_type: ScheduleType | None = None
    schedule_definition: ScheduleDefinition | None = None
    dataset_id: UUID | None = None
    configuration_id: UUID | None = None
    configuration_version: int | None = None
    baseline_run_id: UUID | None = None
    gate_id: UUID | None = None


class EvaluationScheduleResponse(BaseModel):
    """Representation of an evaluation schedule entity."""

    id: UUID
    owner_user_id: UUID
    name: str
    description: str | None
    enabled: bool
    schedule_type: str
    schedule_definition: dict[str, Any]
    dataset_id: UUID
    configuration_id: UUID | None
    configuration_version: int | None
    baseline_run_id: UUID | None
    gate_id: UUID | None
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run_id: UUID | None
    last_status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScheduleExecutionResponse(BaseModel):
    """Execution history record for a schedule."""

    id: UUID
    schedule_id: UUID
    run_id: UUID | None
    started_at: datetime
    completed_at: datetime | None
    execution_status: str
    error_code: str | None
    correlation_id: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
