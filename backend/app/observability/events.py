from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EvaluationEventType(StrEnum):
    """Lifecycle events emitted during evaluation run execution."""

    """Lifecycle and audit events emitted across EVALX."""

    # API Request Events
    REQUEST_STARTED = "request_started"
    REQUEST_COMPLETED = "request_completed"
    REQUEST_FAILED = "request_failed"

    # Evaluation Run Lifecycle Events
    RUN_CREATED = "run_created"
    RUN_STARTED = "run_started"
    CASE_EVALUATION_STARTED = "case_evaluation_started"
    CASE_EVALUATION_COMPLETED = "case_evaluation_completed"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
    RUN_TIMED_OUT = "run_timed_out"

    # Case Execution Events
    CASE_STARTED = "case_started"
    CASE_COMPLETED = "case_completed"
    CASE_FAILED = "case_failed"

    # Evaluator Execution Events
    EVALUATOR_STARTED = "evaluator_started"
    EVALUATOR_COMPLETED = "evaluator_completed"
    EVALUATOR_FAILED = "evaluator_failed"
    EVALUATOR_UNAVAILABLE = "evaluator_unavailable"

    # Configuration Operations
    CONFIG_CREATED = "config_created"
    CONFIG_UPDATED = "config_updated"
    CONFIG_DELETED = "config_deleted"

    # Regression Gate Operations
    GATE_CREATED = "gate_created"
    GATE_UPDATED = "gate_updated"
    GATE_DELETED = "gate_deleted"
    GATE_EVALUATED = "gate_evaluated"

    # Failure Analysis Operations
    ANALYSIS_STARTED = "analysis_started"
    ANALYSIS_COMPLETED = "analysis_completed"
    ANALYSIS_FAILED = "analysis_failed"

    # Queue and Worker Operations
    QUEUE_SUBMITTED = "queue_submitted"
    QUEUE_FAILED = "queue_failed"
    WORKER_STARTED = "worker_started"
    WORKER_COMPLETED = "worker_completed"
    WORKER_FAILED = "worker_failed"

    # Schedule Operations
    SCHEDULE_CREATED = "schedule_created"
    SCHEDULE_UPDATED = "schedule_updated"
    SCHEDULE_DELETED = "schedule_deleted"
    SCHEDULE_ENABLED = "schedule_enabled"
    SCHEDULE_DISABLED = "schedule_disabled"
    SCHEDULE_TRIGGERED = "schedule_triggered"
    SCHEDULE_EXECUTION_STARTED = "schedule_execution_started"
    SCHEDULE_EXECUTION_COMPLETED = "schedule_execution_completed"
    SCHEDULE_EXECUTION_FAILED = "schedule_execution_failed"
    SCHEDULE_MISSED = "schedule_missed"
    SCHEDULE_RETRY = "schedule_retry"


class EvaluationEvent(BaseModel):
    """Structured event capturing an execution milestone or audit action."""

    event_type: EvaluationEventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str | None = None
    actor_user_id: UUID | None = None
    owner_user_id: UUID | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    schedule_id: UUID | None = None
    run_id: UUID | None = None
    dataset_id: UUID | None = None
    case_id: UUID | None = None
    evaluator: str | None = None
    evaluator_backend: str | None = None
    status: str | None = None
    duration_ms: float | None = None
    score: float | None = None
    error: str | None = None
    details: dict[str, Any] | None = None
    worker_id: str | None = None
    job_id: str | None = None
    attempt: int | None = None

    model_config = ConfigDict(frozen=True)
