"""Thread-safe application metrics collector for production diagnosis.

Tracks bounded metrics across API, Evaluation, Queue, and Security domains.
Strictly adheres to bounded-cardinality constraints: no raw identifiers,
prompts, candidate responses, or arbitrary user data are used as metric labels.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from app.observability.events import EvaluationEvent, EvaluationEventType


@dataclass
class ApiMetrics:
    requests_total: int = 0
    request_duration_ms_total: float = 0.0
    status_4xx_total: int = 0
    status_5xx_total: int = 0
    methods: dict[str, int] = field(default_factory=dict)
    status_codes: dict[str, int] = field(default_factory=dict)


@dataclass
class EvaluationMetrics:
    runs_created_total: int = 0
    runs_started_total: int = 0
    runs_completed_total: int = 0
    runs_failed_total: int = 0
    runs_cancelled_total: int = 0
    runs_timed_out_total: int = 0
    run_duration_ms_total: float = 0.0
    case_evaluations_total: int = 0
    evaluator_success_total: int = 0
    evaluator_error_total: int = 0
    evaluator_unavailable_total: int = 0
    evaluator_duration_ms_total: float = 0.0
    evaluator_types: dict[str, int] = field(default_factory=dict)


@dataclass
class QueueMetrics:
    jobs_submitted_total: int = 0
    queue_failures_total: int = 0
    worker_executions_total: int = 0
    retry_count_total: int = 0
    worker_duration_ms_total: float = 0.0


@dataclass
class SecurityMetrics:
    rate_limit_hits_total: int = 0
    rate_limiter_degraded_total: int = 0
    request_body_rejections_total: int = 0


@dataclass
class ScheduleMetrics:
    schedules_created_total: int = 0
    schedules_triggered_total: int = 0
    executions_started_total: int = 0
    executions_completed_total: int = 0
    executions_failed_total: int = 0
    executions_missed_total: int = 0


class MetricsCollector:
    """Thread-safe in-process metrics registry and aggregator.

    Provides atomic counters and bounded summaries for production observability.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.api = ApiMetrics()
        self.evaluation = EvaluationMetrics()
        self.queue = QueueMetrics()
        self.security = SecurityMetrics()
        self.schedule = ScheduleMetrics()

    def record_request(
        self,
        method: str,
        status_code: int,
        duration_ms: float,
    ) -> None:
        """Records API request outcome, status code, and latency."""
        norm_method = (method or "UNKNOWN").upper()[:10]
        code_str = str(status_code)
        with self._lock:
            self.api.requests_total += 1
            self.api.request_duration_ms_total += max(0.0, duration_ms)
            self.api.methods[norm_method] = self.api.methods.get(norm_method, 0) + 1
            self.api.status_codes[code_str] = self.api.status_codes.get(code_str, 0) + 1

            if 400 <= status_code < 500:
                self.api.status_4xx_total += 1
            elif status_code >= 500:
                self.api.status_5xx_total += 1

    def record_event(self, event: EvaluationEvent) -> None:
        """Atomically maps structured evaluation events to relevant metrics."""
        with self._lock:
            et = event.event_type
            if et == EvaluationEventType.RUN_CREATED:
                self.evaluation.runs_created_total += 1
            elif et == EvaluationEventType.RUN_STARTED:
                self.evaluation.runs_started_total += 1
            elif et == EvaluationEventType.RUN_COMPLETED:
                self.evaluation.runs_completed_total += 1
                if event.duration_ms is not None:
                    self.evaluation.run_duration_ms_total += max(0.0, event.duration_ms)
            elif et == EvaluationEventType.RUN_FAILED:
                self.evaluation.runs_failed_total += 1
                if event.duration_ms is not None:
                    self.evaluation.run_duration_ms_total += max(0.0, event.duration_ms)
            elif et == EvaluationEventType.RUN_CANCELLED:
                self.evaluation.runs_cancelled_total += 1
            elif et == EvaluationEventType.RUN_TIMED_OUT:
                self.evaluation.runs_timed_out_total += 1

            elif et in (
                EvaluationEventType.CASE_COMPLETED,
                EvaluationEventType.CASE_EVALUATION_COMPLETED,
            ):
                self.evaluation.case_evaluations_total += 1

            elif et == EvaluationEventType.EVALUATOR_COMPLETED:
                self.evaluation.evaluator_success_total += 1
                if event.evaluator:
                    key = str(event.evaluator)[:50]
                    self.evaluation.evaluator_types[key] = (
                        self.evaluation.evaluator_types.get(key, 0) + 1
                    )
                if event.duration_ms is not None:
                    self.evaluation.evaluator_duration_ms_total += max(
                        0.0, event.duration_ms
                    )
            elif et == EvaluationEventType.EVALUATOR_FAILED:
                self.evaluation.evaluator_error_total += 1
            elif et == EvaluationEventType.EVALUATOR_UNAVAILABLE:
                self.evaluation.evaluator_unavailable_total += 1

            elif et == EvaluationEventType.QUEUE_SUBMITTED:
                self.queue.jobs_submitted_total += 1
            elif et == EvaluationEventType.QUEUE_FAILED:
                self.queue.queue_failures_total += 1
            elif et == EvaluationEventType.WORKER_COMPLETED:
                self.queue.worker_executions_total += 1
                if event.duration_ms is not None:
                    self.queue.worker_duration_ms_total += max(0.0, event.duration_ms)
                if event.attempt and event.attempt > 1:
                    self.queue.retry_count_total += event.attempt - 1
            elif et == EvaluationEventType.WORKER_FAILED:
                self.queue.worker_executions_total += 1

            elif et == EvaluationEventType.SCHEDULE_CREATED:
                self.schedule.schedules_created_total += 1
            elif et == EvaluationEventType.SCHEDULE_TRIGGERED:
                self.schedule.schedules_triggered_total += 1
            elif et == EvaluationEventType.SCHEDULE_EXECUTION_STARTED:
                self.schedule.executions_started_total += 1
            elif et == EvaluationEventType.SCHEDULE_EXECUTION_COMPLETED:
                self.schedule.executions_completed_total += 1
            elif et == EvaluationEventType.SCHEDULE_EXECUTION_FAILED:
                self.schedule.executions_failed_total += 1
            elif et == EvaluationEventType.SCHEDULE_MISSED:
                self.schedule.executions_missed_total += 1

    def record_rate_limit_hit(self) -> None:
        with self._lock:
            self.security.rate_limit_hits_total += 1

    def record_rate_limiter_degraded(self) -> None:
        with self._lock:
            self.security.rate_limiter_degraded_total += 1

    def record_request_body_rejection(self) -> None:
        with self._lock:
            self.security.request_body_rejections_total += 1

    def record_queue_job_submitted(self) -> None:
        with self._lock:
            self.queue.jobs_submitted_total += 1

    def record_queue_failure(self) -> None:
        with self._lock:
            self.queue.queue_failures_total += 1

    def record_worker_execution(
        self, duration_ms: float, success: bool, attempt: int = 1
    ) -> None:
        with self._lock:
            self.queue.worker_executions_total += 1
            self.queue.worker_duration_ms_total += max(0.0, duration_ms)
            if attempt > 1:
                self.queue.retry_count_total += attempt - 1

    def get_snapshot(self) -> dict[str, Any]:
        """Returns an immutable dictionary snapshot of current metrics."""
        with self._lock:
            req_count = self.api.requests_total
            req_dur_avg = (
                round(self.api.request_duration_ms_total / req_count, 2)
                if req_count > 0
                else 0.0
            )

            completed_runs = self.evaluation.runs_completed_total
            run_dur_avg = (
                round(self.evaluation.run_duration_ms_total / completed_runs, 2)
                if completed_runs > 0
                else 0.0
            )

            worker_execs = self.queue.worker_executions_total
            worker_dur_avg = (
                round(self.queue.worker_duration_ms_total / worker_execs, 2)
                if worker_execs > 0
                else 0.0
            )

            return {
                "api": {
                    "requests_total": self.api.requests_total,
                    "request_duration_ms_total": round(
                        self.api.request_duration_ms_total, 2
                    ),
                    "request_duration_ms_avg": req_dur_avg,
                    "status_4xx_total": self.api.status_4xx_total,
                    "status_5xx_total": self.api.status_5xx_total,
                    "methods": dict(self.api.methods),
                    "status_codes": dict(self.api.status_codes),
                },
                "evaluation": {
                    "runs_created_total": self.evaluation.runs_created_total,
                    "runs_started_total": self.evaluation.runs_started_total,
                    "runs_completed_total": self.evaluation.runs_completed_total,
                    "runs_failed_total": self.evaluation.runs_failed_total,
                    "runs_cancelled_total": self.evaluation.runs_cancelled_total,
                    "runs_timed_out_total": self.evaluation.runs_timed_out_total,
                    "run_duration_ms_total": round(
                        self.evaluation.run_duration_ms_total, 2
                    ),
                    "run_duration_ms_avg": run_dur_avg,
                    "case_evaluations_total": self.evaluation.case_evaluations_total,
                    "evaluator_success_total": self.evaluation.evaluator_success_total,
                    "evaluator_error_total": self.evaluation.evaluator_error_total,
                    "evaluator_unavailable_total": (
                        self.evaluation.evaluator_unavailable_total
                    ),
                    "evaluator_duration_ms_total": round(
                        self.evaluation.evaluator_duration_ms_total, 2
                    ),
                    "evaluator_types": dict(self.evaluation.evaluator_types),
                },
                "queue": {
                    "jobs_submitted_total": self.queue.jobs_submitted_total,
                    "queue_failures_total": self.queue.queue_failures_total,
                    "worker_executions_total": self.queue.worker_executions_total,
                    "retry_count_total": self.queue.retry_count_total,
                    "worker_duration_ms_total": round(
                        self.queue.worker_duration_ms_total, 2
                    ),
                    "worker_duration_ms_avg": worker_dur_avg,
                },
                "security": {
                    "rate_limit_hits_total": self.security.rate_limit_hits_total,
                    "rate_limiter_degraded_total": (
                        self.security.rate_limiter_degraded_total
                    ),
                    "request_body_rejections_total": (
                        self.security.request_body_rejections_total
                    ),
                },
                "schedule": {
                    "schedules_created_total": self.schedule.schedules_created_total,
                    "schedules_triggered_total": (
                        self.schedule.schedules_triggered_total
                    ),
                    "executions_started_total": (
                        self.schedule.executions_started_total
                    ),
                    "executions_completed_total": (
                        self.schedule.executions_completed_total
                    ),
                    "executions_failed_total": (self.schedule.executions_failed_total),
                    "executions_missed_total": (self.schedule.executions_missed_total),
                },
            }

    def reset_for_tests(self) -> None:
        """Resets all metrics counters to zero for testing."""
        with self._lock:
            self.api = ApiMetrics()
            self.evaluation = EvaluationMetrics()
            self.queue = QueueMetrics()
            self.security = SecurityMetrics()
            self.schedule = ScheduleMetrics()


# Process-wide default collector instance
default_metrics_collector = MetricsCollector()
