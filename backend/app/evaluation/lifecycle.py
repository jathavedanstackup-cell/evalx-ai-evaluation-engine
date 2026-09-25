from enum import StrEnum

from app.evaluation.errors import InvalidRunStateTransitionError


class RunStatus(StrEnum):
    """Lifecycle state machine for EvaluationRun.

    Explicitly decoupled from individual metric status (MetricStatus).
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


VALID_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.RUNNING: {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


def validate_run_transition(
    current: RunStatus | str, target: RunStatus | str
) -> RunStatus:
    """Validates and returns the legal target RunStatus.

    Raises InvalidRunStateTransitionError if the requested transition is illegal.
    """
    try:
        curr_status = (
            current if isinstance(current, RunStatus) else RunStatus(str(current))
        )
    except ValueError as err:
        raise InvalidRunStateTransitionError(
            f"Unknown current run status '{current}'"
        ) from err

    try:
        tgt_status = target if isinstance(target, RunStatus) else RunStatus(str(target))
    except ValueError as err:
        raise InvalidRunStateTransitionError(
            f"Unknown target run status '{target}'"
        ) from err

    allowed = VALID_TRANSITIONS.get(curr_status, set())
    if tgt_status not in allowed:
        raise InvalidRunStateTransitionError(
            f"Illegal transition from '{curr_status.value}' to '{tgt_status.value}'. "
            f"Allowed transitions: {[s.value for s in allowed]}",
            details={
                "current_status": curr_status.value,
                "target_status": tgt_status.value,
                "allowed_targets": [s.value for s in allowed],
            },
        )
    return tgt_status
