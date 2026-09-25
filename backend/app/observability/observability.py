from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.observability.events import EvaluationEvent
from app.observability.logging import get_structured_logger
from app.observability.metrics import default_metrics_collector
from app.observability.sanitization import sanitize_for_observability

default_logger = get_structured_logger("evalx.observability")


class EvaluationObservability:
    """Instance-based production observability layer for evaluation execution.

    Thread-safe and decoupled: does not use global mutable state.
    Guarantees that event emission failures never break or interrupt evaluation
    execution.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or default_logger
        self._subscribers: list[Callable[[EvaluationEvent], Any]] = []

    def add_subscriber(self, callback: Callable[[EvaluationEvent], Any]) -> None:
        """Registers an in-memory subscriber callback (e.g. for testing/telemetry)."""

        self._subscribers.append(callback)

    def subscribe(self, callback: Callable[[EvaluationEvent], Any]) -> None:
        """Alias for add_subscriber."""
        self.add_subscriber(callback)

    def emit(self, event: EvaluationEvent) -> None:
        """Safely dispatches an event to the logger sink, metrics, and subscribers.

        All operations are protected: exceptions in logging formatting,
        metrics recording, or subscriber callbacks are caught and contained
        without failing the calling process.
        """
        try:
            # 1. Dispatch to metrics collector
            default_metrics_collector.record_event(event)
        except Exception:
            pass

        try:
            # 2. Dispatch to structured logger
            self._log_event(event)
        except Exception:
            # Observability logging failure must NEVER crash evaluation
            pass

        # 3. Dispatch to subscribers
        for subscriber in list(self._subscribers):
            try:
                subscriber(event)
            except Exception:
                # Subscriber callback failure must NEVER crash evaluation
                pass

    def _log_event(self, event: EvaluationEvent) -> None:
        safe_details = (
            sanitize_for_observability(event.details, omit_prompts=True)
            if event.details
            else None
        )
        safe_error = (
            sanitize_for_observability({"error": event.error}, omit_prompts=True).get(
                "error"
            )
            if event.error
            else None
        )

        log_payload = {
            "event_type": event.event_type.value,
            "correlation_id": event.correlation_id,
            "run_id": str(event.run_id) if event.run_id else None,
            "dataset_id": str(event.dataset_id) if event.dataset_id else None,
            "case_id": str(event.case_id) if event.case_id else None,
            "evaluator": event.evaluator,
            "evaluator_backend": event.evaluator_backend,
            "status": event.status,
            "duration_ms": event.duration_ms,
            "score": event.score,
            "error": safe_error,
            "details": safe_details,
            "worker_id": event.worker_id,
            "job_id": event.job_id,
            "attempt": event.attempt,
        }

        # Format clean human-readable log line with key identifiers
        parts = [f"[{event.event_type.value.upper()}]"]
        if event.correlation_id:
            parts.append(f"corr_id={event.correlation_id}")
        if event.run_id:
            parts.append(f"run_id={event.run_id}")
        if event.case_id:
            parts.append(f"case_id={event.case_id}")
        if event.worker_id:
            parts.append(f"worker={event.worker_id}")
        if event.job_id:
            parts.append(f"job={event.job_id}")
        if event.status:
            parts.append(f"status={event.status}")
        if event.duration_ms is not None:
            parts.append(f"duration={event.duration_ms:.2f}ms")
        if event.score is not None:
            parts.append(f"score={event.score:.4f}")

        msg = " ".join(parts)
        is_error = bool(
            event.error
            or (event.status and event.status.lower() in ("error", "failed"))
        )
        if is_error:
            self._logger.warning(msg, extra={"evalx_event": log_payload})
        else:
            self._logger.info(msg, extra={"evalx_event": log_payload})
