import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.lifecycle import RunStatus
from app.evaluation.snapshot import compute_gate_snapshot
from app.models.evaluation_configuration import EvaluationConfig
from app.models.evaluation_run import EvaluationRun
from app.models.regression_gate import (
    RegressionGate,
    RegressionGateResult,
    RegressionGateVersion,
)
from app.schemas.regression_gate import (
    GateEvaluateRequest,
    GateOperator,
    GateOverallStatus,
    GateRuleInput,
    GateRuleResult,
    GateSeverity,
    RegressionGateCreate,
    RegressionGateUpdate,
    RuleEvaluationStatus,
)
from app.services.evaluation_run_service import (
    compare_evaluation_runs,
    get_evaluation_run,
)


def _compare_op(val: float, op: GateOperator | str, thresh: float) -> bool:
    op_str = op.value if isinstance(op, GateOperator) else str(op)
    if op_str == GateOperator.GTE.value:
        return val >= thresh
    if op_str == GateOperator.GT.value:
        return val > thresh
    if op_str == GateOperator.LTE.value:
        return val <= thresh
    if op_str == GateOperator.LT.value:
        return val < thresh
    if op_str == GateOperator.EQ.value:
        return math.isclose(val, thresh, abs_tol=1e-5)
    return False


async def create_regression_gate(
    session: AsyncSession,
    data: RegressionGateCreate,
    owner_user_id: UUID | None = None,
    correlation_id: str | None = None,
) -> RegressionGate:
    """Creates a new RegressionGate and records its initial immutable version."""
    if data.configuration_id is not None:
        cfg_stmt = select(EvaluationConfig).where(
            EvaluationConfig.id == data.configuration_id
        )
        if owner_user_id is not None:
            cfg_stmt = cfg_stmt.where(EvaluationConfig.owner_user_id == owner_user_id)
        if (await session.execute(cfg_stmt)).scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Evaluation configuration {data.configuration_id} not found",
            )

    rules_dicts = [r.model_dump() for r in data.rules]
    _, snapshot_hash = compute_gate_snapshot(
        name=data.name,
        description=data.description,
        version=1,
        rules=rules_dicts,
    )

    gate = RegressionGate(
        owner_user_id=owner_user_id,
        name=data.name,
        description=data.description,
        configuration_id=data.configuration_id,
        version=1,
        enabled=True,
        rules=rules_dicts,
        snapshot_hash=snapshot_hash,
    )
    session.add(gate)
    await session.flush()

    # Initial historical version record
    gate_version = RegressionGateVersion(
        gate_id=gate.id,
        version=1,
        name=data.name,
        description=data.description,
        configuration_id=data.configuration_id,
        rules=rules_dicts,
        snapshot_hash=snapshot_hash,
    )
    session.add(gate_version)

    from app.observability.events import EvaluationEventType
    from app.services import audit_service

    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.GATE_CREATED,
        correlation_id=correlation_id or "evalx-gate",
        resource_type="gate",
        resource_id=gate.id,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        outcome="success",
        metadata={"name": gate.name, "version": gate.version},
    )

    await session.commit()
    await session.refresh(gate)
    return gate


async def get_regression_gate(
    session: AsyncSession,
    gate_id: UUID,
    owner_user_id: UUID | None = None,
) -> RegressionGate | None:
    """Fetch an active regression gate with strict tenant scoping."""
    query = select(RegressionGate).where(RegressionGate.id == gate_id)
    if owner_user_id is not None:
        query = query.where(RegressionGate.owner_user_id == owner_user_id)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def list_regression_gates(
    session: AsyncSession,
    owner_user_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
) -> tuple[list[RegressionGate], int]:
    """List tenant-scoped regression gates with pagination and search."""
    base_query = select(RegressionGate)
    count_query = select(func.count(RegressionGate.id))

    if owner_user_id is not None:
        base_query = base_query.where(RegressionGate.owner_user_id == owner_user_id)
        count_query = count_query.where(RegressionGate.owner_user_id == owner_user_id)

    if search:
        search_pattern = f"%{search.strip()}%"
        base_query = base_query.where(RegressionGate.name.ilike(search_pattern))
        count_query = count_query.where(RegressionGate.name.ilike(search_pattern))

    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    query = (
        base_query.order_by(
            RegressionGate.created_at.desc(),
            RegressionGate.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    items_result = await session.execute(query)
    items = list(items_result.scalars().all())
    return items, total


async def update_regression_gate(
    session: AsyncSession,
    gate_id: UUID,
    data: RegressionGateUpdate,
    owner_user_id: UUID | None = None,
    correlation_id: str | None = None,
) -> RegressionGate | None:
    """Updates a regression gate by incrementing version and saving snapshot."""
    gate = await get_regression_gate(session, gate_id, owner_user_id=owner_user_id)
    if gate is None:
        return None

    if data.configuration_id is not None:
        cfg_stmt = select(EvaluationConfig).where(
            EvaluationConfig.id == data.configuration_id
        )
        if owner_user_id is not None:
            cfg_stmt = cfg_stmt.where(EvaluationConfig.owner_user_id == owner_user_id)
        if (await session.execute(cfg_stmt)).scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Evaluation configuration {data.configuration_id} not found",
            )

    new_name = data.name if data.name is not None else gate.name
    new_desc = data.description if data.description is not None else gate.description
    new_config_id = (
        data.configuration_id
        if data.configuration_id is not None
        else gate.configuration_id
    )
    new_enabled = data.enabled if data.enabled is not None else gate.enabled

    new_rules: list[dict[str, Any]]
    if data.rules is not None:
        new_rules = [r.model_dump() for r in data.rules]
    else:
        new_rules = list(gate.rules)

    new_version = gate.version + 1
    _, snapshot_hash = compute_gate_snapshot(
        name=new_name,
        description=new_desc,
        version=new_version,
        rules=new_rules,
    )

    gate.name = new_name
    gate.description = new_desc
    gate.configuration_id = new_config_id
    gate.enabled = new_enabled
    gate.rules = new_rules
    gate.version = new_version
    gate.snapshot_hash = snapshot_hash

    gate_version = RegressionGateVersion(
        gate_id=gate.id,
        version=new_version,
        name=new_name,
        description=new_desc,
        configuration_id=new_config_id,
        rules=new_rules,
        snapshot_hash=snapshot_hash,
    )
    session.add(gate_version)

    from app.observability.events import EvaluationEventType
    from app.services import audit_service

    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.GATE_UPDATED,
        correlation_id=correlation_id or "evalx-gate",
        resource_type="gate",
        resource_id=gate.id,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        outcome="success",
        metadata={"name": gate.name, "version": gate.version},
    )

    await session.commit()
    await session.refresh(gate)
    return gate


async def delete_regression_gate(
    session: AsyncSession,
    gate_id: UUID,
    owner_user_id: UUID | None = None,
    correlation_id: str | None = None,
) -> bool:
    """Deletes a regression gate, cascading to historical versions."""
    gate = await get_regression_gate(session, gate_id, owner_user_id=owner_user_id)
    if gate is None:
        return False

    from app.observability.events import EvaluationEventType
    from app.services import audit_service

    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.GATE_DELETED,
        correlation_id=correlation_id or "evalx-gate",
        resource_type="gate",
        resource_id=gate_id,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        outcome="success",
    )

    await session.delete(gate)
    await session.commit()
    return True


async def list_gate_versions(
    session: AsyncSession,
    gate_id: UUID,
    owner_user_id: UUID | None = None,
) -> list[RegressionGateVersion] | None:
    """Lists all historical versions for a regression gate."""
    gate = await get_regression_gate(session, gate_id, owner_user_id=owner_user_id)
    if gate is None:
        return None

    stmt = (
        select(RegressionGateVersion)
        .where(RegressionGateVersion.gate_id == gate_id)
        .order_by(RegressionGateVersion.version.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_gate_version(
    session: AsyncSession,
    gate_id: UUID,
    version: int,
    owner_user_id: UUID | None = None,
) -> RegressionGateVersion | None:
    """Fetch a specific historical gate version."""
    gate = await get_regression_gate(session, gate_id, owner_user_id=owner_user_id)
    if gate is None:
        return None

    stmt = select(RegressionGateVersion).where(
        RegressionGateVersion.gate_id == gate_id,
        RegressionGateVersion.version == version,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def evaluate_regression_gate(
    session: AsyncSession,
    gate_id: UUID,
    data: GateEvaluateRequest,
    owner_user_id: UUID | None = None,
    correlation_id: str | None = None,
) -> RegressionGateResult:
    """Evaluates a regression gate against a target run and optional baseline."""
    gate = await get_regression_gate(session, gate_id, owner_user_id=owner_user_id)
    if gate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Regression gate not found",
        )

    target_run = await get_evaluation_run(
        session, data.target_run_id, owner_user_id=owner_user_id
    )
    if target_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target evaluation run not found",
        )

    if target_run.status != RunStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Target evaluation run {target_run.id} is not completed "
                f"(status: {target_run.status})"
            ),
        )

    baseline_run: EvaluationRun | None = None
    comparison = None
    if data.baseline_run_id is not None:
        baseline_run = await get_evaluation_run(
            session, data.baseline_run_id, owner_user_id=owner_user_id
        )
        if baseline_run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Baseline evaluation run not found",
            )
        if baseline_run.status != RunStatus.COMPLETED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Baseline evaluation run {baseline_run.id} is not completed "
                    f"(status: {baseline_run.status})"
                ),
            )
        if owner_user_id is not None:
            comparison = await compare_evaluation_runs(
                session,
                base_run_id=data.baseline_run_id,
                target_run_id=data.target_run_id,
                owner_user_id=owner_user_id,
            )

    gate_snapshot, snapshot_hash = compute_gate_snapshot(
        name=gate.name,
        description=gate.description,
        version=gate.version,
        rules=gate.rules,
    )

    rule_results: list[GateRuleResult] = []

    for r_raw in gate.rules:
        r = GateRuleInput(
            metric_name=r_raw["metric_name"],
            operator=GateOperator(r_raw["operator"]),
            threshold=float(r_raw["threshold"]),
            is_delta=bool(r_raw.get("is_delta", False)),
            severity=GateSeverity(r_raw.get("severity", GateSeverity.CRITICAL.value)),
            enabled=bool(r_raw.get("enabled", True)),
        )

        if not r.enabled:
            rule_results.append(
                GateRuleResult(
                    metric_name=r.metric_name,
                    operator=r.operator,
                    threshold=r.threshold,
                    is_delta=r.is_delta,
                    severity=r.severity,
                    target_value=None,
                    baseline_value=None,
                    delta=None,
                    status=RuleEvaluationStatus.SKIPPED,
                    reason="Rule is disabled",
                )
            )
            continue

        if r.is_delta:
            if comparison is None:
                rule_results.append(
                    GateRuleResult(
                        metric_name=r.metric_name,
                        operator=r.operator,
                        threshold=r.threshold,
                        is_delta=r.is_delta,
                        severity=r.severity,
                        target_value=None,
                        baseline_value=None,
                        delta=None,
                        status=RuleEvaluationStatus.INCONCLUSIVE,
                        reason=(
                            "Delta rule requires a baseline run, but none was provided"
                        ),
                    )
                )
                continue

            delta_val: float | None = None
            target_val: float | None = None
            base_val: float | None = None

            if r.metric_name == "overall_score":
                delta_val = comparison.summary.overall_score_delta
                target_val = comparison.summary.target_overall_score
                base_val = comparison.summary.base_overall_score
            else:
                m_comp = comparison.summary.metric_comparisons.get(r.metric_name)
                if m_comp is not None:
                    delta_val = m_comp.delta
                    target_val = m_comp.target_score
                    base_val = m_comp.base_score

            if delta_val is None:
                rule_results.append(
                    GateRuleResult(
                        metric_name=r.metric_name,
                        operator=r.operator,
                        threshold=r.threshold,
                        is_delta=r.is_delta,
                        severity=r.severity,
                        target_value=target_val,
                        baseline_value=base_val,
                        delta=None,
                        status=RuleEvaluationStatus.INCONCLUSIVE,
                        reason=(
                            f"Metric '{r.metric_name}' delta is unavailable "
                            "between baseline and target runs"
                        ),
                    )
                )
                continue

            passed = _compare_op(delta_val, r.operator, r.threshold)
            status_enum = (
                RuleEvaluationStatus.PASS if passed else RuleEvaluationStatus.FAIL
            )
            reason_str = (
                f"Delta {delta_val:.4f} satisfies {r.operator.value} {r.threshold}"
                if passed
                else f"Delta {delta_val:.4f} violated {r.operator.value} {r.threshold}"
            )

            rule_results.append(
                GateRuleResult(
                    metric_name=r.metric_name,
                    operator=r.operator,
                    threshold=r.threshold,
                    is_delta=r.is_delta,
                    severity=r.severity,
                    target_value=target_val,
                    baseline_value=base_val,
                    delta=delta_val,
                    status=status_enum,
                    reason=reason_str,
                )
            )

        else:
            # Absolute metric evaluation
            target_val_abs: float | None = None
            if r.metric_name == "overall_score":
                target_val_abs = target_run.overall_score
            else:
                metrics_sum = target_run.metrics_summary or {}
                metric_avgs = metrics_sum.get("metric_averages", {})
                if r.metric_name in metric_avgs:
                    target_val_abs = metric_avgs.get(r.metric_name)
                elif r.metric_name in metrics_sum and isinstance(
                    metrics_sum[r.metric_name], int | float
                ):
                    target_val_abs = float(metrics_sum[r.metric_name])

            if target_val_abs is None:
                rule_results.append(
                    GateRuleResult(
                        metric_name=r.metric_name,
                        operator=r.operator,
                        threshold=r.threshold,
                        is_delta=r.is_delta,
                        severity=r.severity,
                        target_value=None,
                        baseline_value=None,
                        delta=None,
                        status=RuleEvaluationStatus.INCONCLUSIVE,
                        reason=(
                            f"Metric '{r.metric_name}' not found in target run summary"
                        ),
                    )
                )
                continue

            passed = _compare_op(target_val_abs, r.operator, r.threshold)
            status_enum = (
                RuleEvaluationStatus.PASS if passed else RuleEvaluationStatus.FAIL
            )
            reason_str = (
                f"Value {target_val_abs:.4f} satisfies {r.operator.value} {r.threshold}"
                if passed
                else (
                    f"Value {target_val_abs:.4f} violated "
                    f"{r.operator.value} {r.threshold}"
                )
            )

            rule_results.append(
                GateRuleResult(
                    metric_name=r.metric_name,
                    operator=r.operator,
                    threshold=r.threshold,
                    is_delta=r.is_delta,
                    severity=r.severity,
                    target_value=target_val_abs,
                    baseline_value=None,
                    delta=None,
                    status=status_enum,
                    reason=reason_str,
                )
            )

    # Determine overall gate decision
    active_critical = [
        res
        for res in rule_results
        if res.severity == GateSeverity.CRITICAL
        and res.status != RuleEvaluationStatus.SKIPPED
    ]

    if any(res.status == RuleEvaluationStatus.FAIL for res in active_critical):
        overall_status = GateOverallStatus.FAIL
    elif any(
        res.status == RuleEvaluationStatus.INCONCLUSIVE for res in active_critical
    ):
        overall_status = GateOverallStatus.INCONCLUSIVE
    else:
        overall_status = GateOverallStatus.PASS

    passed_crit = len(
        [r for r in active_critical if r.status == RuleEvaluationStatus.PASS]
    )
    summary_text = (
        f"Gate '{gate.name}' (v{gate.version}) evaluated with status "
        f"{overall_status.value.upper()}. "
        f"Critical rules passed: {passed_crit}/{len(active_critical)}."
    )

    gate_result = RegressionGateResult(
        gate_id=gate.id,
        gate_version=gate.version,
        target_run_id=target_run.id,
        baseline_run_id=data.baseline_run_id,
        gate_snapshot=gate_snapshot,
        snapshot_hash=snapshot_hash,
        overall_status=overall_status.value,
        rule_results=[r.model_dump() for r in rule_results],
        summary=summary_text,
        correlation_id=correlation_id,
        evaluated_at=datetime.now(UTC),
    )
    session.add(gate_result)

    from app.observability.events import EvaluationEventType
    from app.services import audit_service

    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.GATE_EVALUATED,
        correlation_id=correlation_id or "evalx-gate-eval",
        resource_type="gate",
        resource_id=gate.id,
        run_id=target_run.id,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        outcome=overall_status.value,
        metadata={
            "gate_id": str(gate.id),
            "status": overall_status.value,
            "target_run_id": str(target_run.id),
        },
    )

    await session.commit()
    await session.refresh(gate_result)
    return gate_result


async def list_gate_evaluations(
    session: AsyncSession,
    gate_id: UUID,
    owner_user_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[RegressionGateResult], int]:
    """Lists historical evaluation results for a regression gate with pagination."""
    gate = await get_regression_gate(session, gate_id, owner_user_id=owner_user_id)
    if gate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Regression gate not found",
        )

    base_query = select(RegressionGateResult).where(
        RegressionGateResult.gate_id == gate_id
    )
    count_query = select(func.count(RegressionGateResult.id)).where(
        RegressionGateResult.gate_id == gate_id
    )

    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    query = (
        base_query.order_by(
            RegressionGateResult.evaluated_at.desc(),
            RegressionGateResult.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    items_result = await session.execute(query)
    items = list(items_result.scalars().all())
    return items, total


async def get_gate_evaluation(
    session: AsyncSession,
    evaluation_id: UUID,
    owner_user_id: UUID | None = None,
) -> RegressionGateResult | None:
    """Fetch a specific regression gate evaluation outcome with tenant check."""
    query = (
        select(RegressionGateResult)
        .join(
            RegressionGate,
            RegressionGateResult.gate_id == RegressionGate.id,
        )
        .where(RegressionGateResult.id == evaluation_id)
    )
    if owner_user_id is not None:
        query = query.where(RegressionGate.owner_user_id == owner_user_id)

    result = await session.execute(query)
    return result.scalar_one_or_none()
