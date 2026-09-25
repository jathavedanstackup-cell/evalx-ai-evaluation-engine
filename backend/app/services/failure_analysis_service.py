from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.lifecycle import RunStatus
from app.evaluation.snapshot import compute_analysis_snapshot
from app.models.evaluation_result import EvaluationResult
from app.models.evaluation_run import EvaluationRun
from app.models.run_analysis import RunAnalysis
from app.schemas.failure_analysis import (
    BaselineRegressionInsights,
    CaseFailureAnalysis,
    CaseFailureSummary,
    CategoryDistribution,
    FailureCategory,
    MetricDistribution,
    MetricFailureSummary,
    MetricThresholdFailureDetail,
    RunAnalysisResponse,
    RunInsights,
)
from app.services.evaluation_run_service import get_evaluation_run


def classify_case_failures(
    result: EvaluationResult,
    baseline_result: EvaluationResult | None = None,
) -> CaseFailureAnalysis:
    """Classifies failures and extracts diagnostics for a single case."""
    overall_score = (
        round(result.overall_score, 4) if result.overall_score is not None else None
    )
    passed = bool(result.passed)
    execution_status = result.status or "completed"

    failed_metrics_set: set[str] = set()
    failure_categories_set: set[FailureCategory] = set()
    metric_scores: dict[str, float | None] = {}
    metric_statuses: dict[str, str] = {}
    threshold_failures: list[MetricThresholdFailureDetail] = []

    # 1. Output completeness & parsing checks
    if result.response is None or not str(result.response).strip():
        failure_categories_set.add(FailureCategory.MISSING_OUTPUT)

    if result.error_message:
        err_lower = result.error_message.lower()
        if any(
            t in err_lower
            for t in ["json", "decode", "malformed", "parse", "format", "schema"]
        ):
            failure_categories_set.add(FailureCategory.MALFORMED_OUTPUT)
        elif (
            not passed
            and FailureCategory.MALFORMED_OUTPUT not in failure_categories_set
        ):
            failure_categories_set.add(FailureCategory.EVALUATOR_ERROR)

    # 2. Metric evaluation inspections
    metrics_data = result.metrics or {}
    for k in sorted(metrics_data.keys()):
        m_dict = metrics_data[k]
        if not isinstance(m_dict, dict):
            continue

        m_name = str(m_dict.get("metric_name") or k)
        m_score = m_dict.get("score")
        m_thresh = m_dict.get("threshold")
        m_status = m_dict.get("status")
        m_passed = m_dict.get("passed")
        m_type = str(m_dict.get("evaluator_type") or m_name).lower()
        m_expl = str(m_dict.get("explanation") or "")

        score_rounded = (
            round(float(m_score), 4) if isinstance(m_score, int | float) else None
        )
        metric_scores[m_name] = score_rounded

        status_raw = getattr(m_status, "value", m_status)
        status_str = str(status_raw or "success").lower()
        metric_statuses[m_name] = status_str

        if status_str == "error":
            failed_metrics_set.add(m_name)
            failure_categories_set.add(FailureCategory.EVALUATOR_ERROR)
        elif status_str == "unavailable":
            failed_metrics_set.add(m_name)
            failure_categories_set.add(FailureCategory.EVALUATOR_UNAVAILABLE)
        elif status_str == "threshold_failed" or m_passed is False:
            failed_metrics_set.add(m_name)
            cat = FailureCategory.METRIC_THRESHOLD_FAILURE
            m_type_clean = m_type.replace("evaluatortype.", "").lower()
            m_name_clean = m_name.lower()

            if "factuality" in m_type_clean or "factuality" in m_name_clean:
                cat = FailureCategory.FACTUALITY_FAILURE
            elif "relevance" in m_type_clean or "relevance" in m_name_clean:
                cat = FailureCategory.RELEVANCE_FAILURE
            elif "faithfulness" in m_type_clean or "faithfulness" in m_name_clean:
                cat = FailureCategory.FAITHFULNESS_FAILURE
            elif "hallucination" in m_type_clean or "hallucination" in m_name_clean:
                cat = FailureCategory.HALLUCINATION_FAILURE
            elif "consistency" in m_type_clean or "consistency" in m_name_clean:
                cat = FailureCategory.CONSISTENCY_FAILURE
            elif "instruction" in m_type_clean or "instruction" in m_name_clean:
                cat = FailureCategory.INSTRUCTION_FAILURE

            failure_categories_set.add(cat)
            thresh_rounded = (
                round(float(m_thresh), 4) if isinstance(m_thresh, int | float) else None
            )
            threshold_failures.append(
                MetricThresholdFailureDetail(
                    metric_name=m_name,
                    score=score_rounded,
                    threshold=thresh_rounded,
                    failure_category=cat,
                    reasoning=m_expl if m_expl else None,
                )
            )

    # 3. Baseline comparison & regression detection
    comparison_delta: float | None = None
    if baseline_result is not None:
        if (
            result.overall_score is not None
            and baseline_result.overall_score is not None
        ):
            comparison_delta = round(
                result.overall_score - baseline_result.overall_score, 4
            )

        if baseline_result.passed is True and not passed:
            failure_categories_set.add(FailureCategory.REGRESSION_FAILURE)

    # Generic fallback when overall case failed but no metric triggered
    if not passed and not failure_categories_set:
        failure_categories_set.add(FailureCategory.METRIC_THRESHOLD_FAILURE)

    # Sort deterministically
    sorted_failed_metrics = sorted(failed_metrics_set)
    sorted_categories = sorted(failure_categories_set, key=lambda c: c.value)
    sorted_threshold_failures = sorted(threshold_failures, key=lambda t: t.metric_name)

    return CaseFailureAnalysis(
        case_id=result.case_id,
        overall_case_score=overall_score,
        passed=passed,
        failed_metrics=sorted_failed_metrics,
        failure_categories=sorted_categories,
        metric_scores=metric_scores,
        metric_statuses=metric_statuses,
        threshold_failures=sorted_threshold_failures,
        execution_status=execution_status,
        comparison_delta=comparison_delta,
    )


async def analyze_run_failures(
    session: AsyncSession,
    run_id: UUID,
    owner_user_id: UUID | None = None,
    baseline_run_id: UUID | None = None,
    correlation_id: str | None = None,
) -> RunAnalysisResponse:
    """Executes failure analysis across all cases of an evaluation run."""
    target_run = await get_evaluation_run(session, run_id, owner_user_id=owner_user_id)
    if target_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evaluation run not found",
        )

    allowed_statuses = {RunStatus.COMPLETED.value, RunStatus.FAILED.value}
    if target_run.status not in allowed_statuses:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Target evaluation run {target_run.id} is in non-terminal state "
                f"'{target_run.status}' (must be 'completed' or 'failed')"
            ),
        )

    baseline_run: EvaluationRun | None = None
    baseline_results_map: dict[UUID, EvaluationResult] = {}
    if baseline_run_id is not None:
        baseline_run = await get_evaluation_run(
            session, baseline_run_id, owner_user_id=owner_user_id
        )
        if baseline_run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Baseline evaluation run not found",
            )
        if baseline_run.status not in allowed_statuses:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Baseline evaluation run {baseline_run.id} is in "
                    f"non-terminal state '{baseline_run.status}' "
                    "(must be 'completed' or 'failed')"
                ),
            )

        base_stmt = (
            select(EvaluationResult)
            .where(EvaluationResult.run_id == baseline_run_id)
            .order_by(EvaluationResult.case_id)
        )
        base_records = (await session.scalars(base_stmt)).all()
        baseline_results_map = {r.case_id: r for r in base_records}

    # Fetch target evaluation results in single query (bounded & sorted)
    target_stmt = (
        select(EvaluationResult)
        .where(EvaluationResult.run_id == run_id)
        .order_by(EvaluationResult.case_id)
    )
    target_results = (await session.scalars(target_stmt)).all()

    total_cases = len(target_results)
    passed_cases = sum(1 for r in target_results if r.passed is True)
    failed_cases = sum(1 for r in target_results if r.passed is False)
    pass_rate = round(passed_cases / total_cases, 4) if total_cases > 0 else 0.0
    failure_rate = round(failed_cases / total_cases, 4) if total_cases > 0 else 0.0

    # Analyse individual cases
    failed_case_analyses: list[CaseFailureAnalysis] = []
    category_counts: dict[FailureCategory, int] = defaultdict(int)

    metric_success_counts: dict[str, int] = defaultdict(int)
    metric_threshold_failed_counts: dict[str, int] = defaultdict(int)
    metric_error_counts: dict[str, int] = defaultdict(int)
    metric_unavailable_counts: dict[str, int] = defaultdict(int)
    metric_skipped_counts: dict[str, int] = defaultdict(int)
    metric_score_sums: dict[str, float] = defaultdict(float)
    metric_score_counts: dict[str, int] = defaultdict(int)

    for r in target_results:
        b_res = baseline_results_map.get(r.case_id)
        analysis = classify_case_failures(r, baseline_result=b_res)

        for cat in analysis.failure_categories:
            category_counts[cat] += 1

        if not analysis.passed:
            failed_case_analyses.append(analysis)

        # Aggregate metric statistics
        metrics_dict = r.metrics or {}
        for m_key, m_val in metrics_dict.items():
            if not isinstance(m_val, dict):
                continue
            m_name = str(m_val.get("metric_name") or m_key)
            status_obj = m_val.get("status")
            status_raw = getattr(status_obj, "value", status_obj)
            m_status = str(status_raw or "success").lower()
            m_score = m_val.get("score")
            m_passed = m_val.get("passed")

            if isinstance(m_score, int | float):
                metric_score_sums[m_name] += float(m_score)
                metric_score_counts[m_name] += 1

            if m_status == "error":
                metric_error_counts[m_name] += 1
            elif m_status == "unavailable":
                metric_unavailable_counts[m_name] += 1
            elif m_status == "skipped":
                metric_skipped_counts[m_name] += 1
            elif m_status == "threshold_failed" or m_passed is False:
                metric_threshold_failed_counts[m_name] += 1
            else:
                metric_success_counts[m_name] += 1

    # 1. Metric Distributions
    all_metrics = sorted(
        set(metric_success_counts.keys())
        | set(metric_threshold_failed_counts.keys())
        | set(metric_error_counts.keys())
        | set(metric_unavailable_counts.keys())
        | set(metric_skipped_counts.keys())
    )

    metric_distributions: list[MetricDistribution] = []
    metric_averages: dict[str, float | None] = {}
    metric_failure_counts: dict[str, int] = {}
    metric_threshold_failure_rates: dict[str, float] = {}

    for m in all_metrics:
        succ = metric_success_counts[m]
        th_fail = metric_threshold_failed_counts[m]
        err = metric_error_counts[m]
        unavail = metric_unavailable_counts[m]
        skip = metric_skipped_counts[m]
        evaluated = succ + th_fail + err + unavail

        fail_pct = (
            round((th_fail + err + unavail) / evaluated * 100.0, 2)
            if evaluated > 0
            else 0.0
        )
        avg_score = (
            round(metric_score_sums[m] / metric_score_counts[m], 4)
            if metric_score_counts[m] > 0
            else None
        )

        metric_averages[m] = avg_score
        metric_failure_counts[m] = th_fail + err + unavail
        metric_threshold_failure_rates[m] = (
            round(th_fail / evaluated, 4) if evaluated > 0 else 0.0
        )

        metric_distributions.append(
            MetricDistribution(
                metric_name=m,
                successful_evaluations=succ,
                threshold_failed=th_fail,
                error=err,
                unavailable=unavail,
                skipped=skip,
                failure_percentage=fail_pct,
                average_score=avg_score,
            )
        )

    # 2. Category Distributions
    all_categories = sorted(list(FailureCategory), key=lambda c: c.value)
    category_distributions: list[CategoryDistribution] = []
    for cat in all_categories:
        cnt = category_counts[cat]
        pct = round(cnt / total_cases * 100.0, 2) if total_cases > 0 else 0.0
        category_distributions.append(
            CategoryDistribution(
                category=cat,
                count=cnt,
                percentage_of_cases=pct,
            )
        )

    # 3. Most affected metrics & cases
    most_affected_metrics = [
        MetricFailureSummary(
            metric_name=dist.metric_name,
            failure_count=dist.threshold_failed + dist.error + dist.unavailable,
            failure_rate=round((dist.failure_percentage or 0.0) / 100.0, 4),
        )
        for dist in sorted(
            metric_distributions,
            key=lambda d: (
                -(d.threshold_failed + d.error + d.unavailable),
                -(d.failure_percentage or 0.0),
                d.metric_name,
            ),
        )
        if (dist.threshold_failed + dist.error + dist.unavailable) > 0
    ]

    most_affected_cases = [
        CaseFailureSummary(
            case_id=c.case_id,
            failed_metric_count=len(c.failed_metrics),
            failed_metrics=c.failed_metrics,
            overall_score=c.overall_case_score,
        )
        for c in sorted(
            failed_case_analyses,
            key=lambda a: (
                -len(a.failed_metrics),
                a.overall_case_score if a.overall_case_score is not None else 1.0,
                str(a.case_id),
            ),
        )
    ]

    # 4. Baseline Regression Insights (if baseline provided)
    baseline_insights: BaselineRegressionInsights | None = None
    if baseline_run is not None and baseline_run_id is not None:
        target_map = {r.case_id: r for r in target_results}
        common_case_ids = sorted(
            set(target_map.keys()) & set(baseline_results_map.keys())
        )

        newly_failing: list[UUID] = []
        newly_passing: list[UUID] = []
        unchanged_failures: list[UUID] = []
        unchanged_passes: list[UUID] = []

        base_category_counts: dict[FailureCategory, int] = defaultdict(int)
        for b_rec in baseline_results_map.values():
            b_diag = classify_case_failures(b_rec)
            for cat in b_diag.failure_categories:
                base_category_counts[cat] += 1

        for c_id in common_case_ids:
            t_r = target_map[c_id]
            b_r = baseline_results_map[c_id]
            if b_r.passed is True and t_r.passed is False:
                newly_failing.append(c_id)
            elif b_r.passed is False and t_r.passed is True:
                newly_passing.append(c_id)
            elif b_r.passed is False and t_r.passed is False:
                unchanged_failures.append(c_id)
            elif b_r.passed is True and t_r.passed is True:
                unchanged_passes.append(c_id)

        # Metric average deltas
        metric_deltas: dict[str, float | None] = {}
        target_sum = target_run.metrics_summary or {}
        base_sum = baseline_run.metrics_summary or {}
        t_avgs = target_sum.get("metric_averages", {})
        b_avgs = base_sum.get("metric_averages", {})
        for m in sorted(set(t_avgs.keys()) | set(b_avgs.keys())):
            t_val = t_avgs.get(m)
            b_val = b_avgs.get(m)
            if t_val is not None and b_val is not None:
                metric_deltas[m] = round(float(t_val) - float(b_val), 4)
            else:
                metric_deltas[m] = None

        category_deltas = {
            cat.value: category_counts[cat] - base_category_counts[cat]
            for cat in all_categories
        }

        reg_rate = (
            round(len(newly_failing) / total_cases, 4) if total_cases > 0 else 0.0
        )
        rec_rate = (
            round(len(newly_passing) / total_cases, 4) if total_cases > 0 else 0.0
        )

        baseline_insights = BaselineRegressionInsights(
            baseline_run_id=baseline_run_id,
            newly_failing_cases=newly_failing,
            newly_passing_cases=newly_passing,
            unchanged_failures=unchanged_failures,
            unchanged_passes=unchanged_passes,
            metric_deltas=metric_deltas,
            failure_category_deltas=category_deltas,
            regression_rate=reg_rate,
            recovery_rate=rec_rate,
        )

    # Construct RunInsights
    run_insights = RunInsights(
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        pass_rate=pass_rate,
        failure_rate=failure_rate,
        metric_averages={
            m: avg for m, avg in metric_averages.items() if avg is not None
        },
        metric_failure_counts=metric_failure_counts,
        metric_threshold_failure_rates=metric_threshold_failure_rates,
        unavailable_counts={
            m: metric_unavailable_counts[m]
            for m in all_metrics
            if metric_unavailable_counts[m] > 0
        },
        error_counts={
            m: metric_error_counts[m] for m in all_metrics if metric_error_counts[m] > 0
        },
        failure_category_counts={
            cat.value: category_counts[cat] for cat in all_categories
        },
        most_affected_metrics=most_affected_metrics,
        most_affected_cases=most_affected_cases,
    )

    summary_text = (
        f"Run {run_id} failure analysis: pass rate {pass_rate * 100.0:.1f}%, "
        f"{failed_cases}/{total_cases} cases failed across "
        f"{len(most_affected_metrics)} failing metrics."
    )
    if baseline_insights:
        summary_text += (
            f" Regression rate: {baseline_insights.regression_rate * 100.0:.1f}%, "
            f"recovery rate: {baseline_insights.recovery_rate * 100.0:.1f}%."
        )

    # Deterministic snapshot & hash
    raw_analysis_dict: dict[str, Any] = {
        "baseline_insights": (
            baseline_insights.model_dump(mode="json") if baseline_insights else None
        ),
        "category_distributions": [
            cd.model_dump(mode="json") for cd in category_distributions
        ],
        "failed_cases": [fc.model_dump(mode="json") for fc in failed_case_analyses],
        "metric_distributions": [
            md.model_dump(mode="json") for md in metric_distributions
        ],
        "run_id": str(run_id),
        "run_insights": run_insights.model_dump(mode="json"),
        "summary": summary_text,
    }

    _, snapshot_hash = compute_analysis_snapshot(
        run_id=run_id,
        baseline_run_id=baseline_run_id,
        analysis_data=raw_analysis_dict,
    )

    # Persist or update RunAnalysis record
    analysis_record = RunAnalysis(
        run_id=run_id,
        baseline_run_id=baseline_run_id,
        snapshot_hash=snapshot_hash,
        analysis_data=raw_analysis_dict,
        summary=summary_text,
        created_at=datetime.now(UTC),
    )
    session.add(analysis_record)

    from app.observability.events import EvaluationEventType
    from app.services import audit_service

    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.ANALYSIS_COMPLETED,
        correlation_id=correlation_id or "evalx-analysis",
        resource_type="analysis",
        resource_id=analysis_record.id,
        run_id=run_id,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        outcome="success",
        metadata={"total_cases": total_cases, "failed_cases": failed_cases},
    )

    await session.commit()
    await session.refresh(analysis_record)

    return RunAnalysisResponse(
        run_id=run_id,
        baseline_run_id=baseline_run_id,
        summary=summary_text,
        run_insights=run_insights,
        metric_distributions=metric_distributions,
        category_distributions=category_distributions,
        failed_cases=failed_case_analyses,
        baseline_insights=baseline_insights,
        snapshot_hash=snapshot_hash,
        correlation_id=correlation_id,
        created_at=analysis_record.created_at,
    )
