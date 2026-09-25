from collections.abc import Sequence

from app.evaluation.enums import MetricStatus
from app.evaluation.types import EvaluationCaseResult, EvaluationMetric

SCORE_MIN: float = 0.0
SCORE_MAX: float = 1.0


def validate_score_range(score: float | None, field_name: str = "score") -> None:
    """Validates that a numeric score is within the documented range [0.0, 1.0]."""
    if score is not None and not (SCORE_MIN <= score <= SCORE_MAX):
        raise ValueError(
            f"{field_name} must be within range [{SCORE_MIN}, {SCORE_MAX}], got {score}"
        )


def evaluate_metric_threshold(
    score: float | None,
    threshold: float | None,
) -> tuple[bool | None, MetricStatus]:
    """Deterministically evaluates a metric score against an explicit threshold.

    Rules:
    - If score is None: passed=None, status=MetricStatus.UNAVAILABLE.
    - If threshold is None: passed=None, status=MetricStatus.SUCCESS.
    - If score >= threshold: passed=True, status=MetricStatus.SUCCESS.
    - If score < threshold: passed=False, status=MetricStatus.THRESHOLD_FAILED.
    """
    if score is None:
        return None, MetricStatus.UNAVAILABLE

    validate_score_range(score, "score")
    if threshold is None:
        return None, MetricStatus.SUCCESS

    validate_score_range(threshold, "threshold")
    passed = score >= threshold
    status = MetricStatus.SUCCESS if passed else MetricStatus.THRESHOLD_FAILED
    return passed, status


def calculate_case_score(
    metrics: Sequence[EvaluationMetric] | dict[str, EvaluationMetric],
    weights: dict[str, float] | None = None,
) -> tuple[float | None, bool | None]:
    """Calculates the deterministic overall score and pass/fail status for a case.

    Scoring Model:
      Overall Score = (Sum of w_i * s_i) / (Sum of w_i)
      where:
        - Only metrics with status SUCCESS or THRESHOLD_FAILED contribute.
        - Missing, unavailable, or errored metrics are excluded from denominator.
        - Default weight is 1.0 for each metric if not configured in `weights`.
        - If no metrics produced a valid score, overall score is None.

    Pass/Fail Rules:
      - If any metric has passed == False or status == ERROR: case passed = False.
      - If at least one metric has passed == True and no failures: case passed = True.
      - If no metrics configured thresholds and no errors: case passed = None.

    Returns:
      (overall_score, passed)
    """
    metric_list = list(metrics.values()) if isinstance(metrics, dict) else list(metrics)
    if not metric_list:
        return None, None

    weights_map = weights or {}
    total_weighted_score = 0.0
    total_weight = 0.0
    has_explicit_fail = False
    has_explicit_pass = False

    for m in metric_list:
        # Check pass/fail flags
        if m.status == MetricStatus.ERROR or m.passed is False:
            has_explicit_fail = True
        elif m.passed is True:
            has_explicit_pass = True

        # Score contribution
        if m.score is not None and m.status in (
            MetricStatus.SUCCESS,
            MetricStatus.THRESHOLD_FAILED,
        ):
            w = weights_map.get(m.metric_name, 1.0)
            if w < 0.0:
                raise ValueError(
                    f"Weight for metric '{m.metric_name}' must be non-negative, got {w}"
                )
            total_weighted_score += m.score * w
            total_weight += w

    overall_score: float | None = None
    if total_weight > 0.0:
        overall_score = round(total_weighted_score / total_weight, 6)

    case_passed: bool | None = None
    if has_explicit_fail:
        case_passed = False
    elif has_explicit_pass:
        case_passed = True

    return overall_score, case_passed


def calculate_run_score(
    case_results: Sequence[EvaluationCaseResult],
) -> tuple[float | None, dict[str, float], int, int]:
    """Aggregates metrics and overall score across all cases in an EvaluationRun.

    Rules:
      - Run Overall Score: arithmetic mean of all non-None case overall scores.
      - Metric Averages: arithmetic mean per metric name across valid scores.
      - Completed cases: count of cases where overall_score is not None and no errors.
      - Failed cases: count of cases with errors or passed == False.

    Returns:
      (overall_run_score, metric_averages, completed_count, failed_count)
    """
    if not case_results:
        return None, {}, 0, 0

    case_scores: list[float] = []
    metric_accumulators: dict[str, list[float]] = {}
    completed_count = 0
    failed_count = 0

    for c in case_results:
        is_failed = (c.passed is False) or bool(c.errors)
        if is_failed:
            failed_count += 1
        elif c.overall_score is not None:
            completed_count += 1

        if c.overall_score is not None:
            case_scores.append(c.overall_score)

        for m_name, m in c.metrics.items():
            if m.score is not None and m.status in (
                MetricStatus.SUCCESS,
                MetricStatus.THRESHOLD_FAILED,
            ):
                metric_accumulators.setdefault(m_name, []).append(m.score)

    overall_run_score: float | None = None
    if case_scores:
        overall_run_score = round(sum(case_scores) / len(case_scores), 6)

    metric_averages: dict[str, float] = {
        m_name: round(sum(scores) / len(scores), 6)
        for m_name, scores in metric_accumulators.items()
        if scores
    }

    return overall_run_score, metric_averages, completed_count, failed_count
