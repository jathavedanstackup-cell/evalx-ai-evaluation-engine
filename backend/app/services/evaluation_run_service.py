import logging
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.evaluation.contracts import BaseEvaluator
from app.evaluation.enums import EvaluatorType, MetricStatus
from app.evaluation.errors import (
    UnsupportedEvaluatorError,
    sanitize_error_message,
)
from app.evaluation.factory import create_evaluator
from app.evaluation.lifecycle import RunStatus, validate_run_transition
from app.evaluation.snapshot import (
    compute_configuration_snapshot,
    compute_dataset_snapshot_hash,
)
from app.evaluation.types import (
    EvaluationCaseResult,
    EvaluationContext,
    EvaluationInput,
    EvaluationRunResult,
)
from app.models.dataset import Dataset, DatasetCase
from app.models.evaluation import Evaluation
from app.models.evaluation_result import EvaluationResult
from app.models.evaluation_run import EvaluationRun
from app.models.evaluator_config import EvaluatorConfig
from app.observability import (
    EvaluationEvent,
    EvaluationEventType,
    EvaluationObservability,
    validate_and_normalize_correlation_id,
    validate_execution_metadata,
)
from app.schemas.evaluation_comparison import (
    CaseComparison,
    CaseComparisonCategory,
    MetricComparison,
    RunComparisonResponse,
    RunComparisonSummary,
)
from app.schemas.evaluation_run import (
    MAX_CASES_PER_RUN,
    MAX_EVALUATORS_PER_RUN,
    CaseResponseInput,
    EvaluationResultResponse,
    EvaluationRunCreate,
    EvaluationRunResponse,
)
from app.services import audit_service

logger = logging.getLogger(__name__)

_sanitize_error_message = sanitize_error_message


def _determine_case_status(case_res: EvaluationCaseResult) -> str:
    """Determines the case-level status string from granular evaluator metrics.

    Precedence rules:
      1. If any metric status == ERROR or non-empty case errors -> 'error'
      2. If any metric status == THRESHOLD_FAILED or passed is False ->
         'threshold_failed'
      3. If all metrics are UNAVAILABLE -> 'unavailable'
      4. If all metrics are SKIPPED -> 'skipped'
      5. Otherwise -> 'success'
    """
    if case_res.errors:
        return MetricStatus.ERROR.value

    metric_statuses = [m.status for m in case_res.metrics.values()]
    if not metric_statuses:
        return MetricStatus.UNAVAILABLE.value

    if any(s == MetricStatus.ERROR for s in metric_statuses):
        return MetricStatus.ERROR.value

    if case_res.passed is False or any(
        s == MetricStatus.THRESHOLD_FAILED for s in metric_statuses
    ):
        return MetricStatus.THRESHOLD_FAILED.value

    if all(s == MetricStatus.UNAVAILABLE for s in metric_statuses):
        return MetricStatus.UNAVAILABLE.value

    if all(s == MetricStatus.SKIPPED for s in metric_statuses):
        return MetricStatus.SKIPPED.value

    return MetricStatus.SUCCESS.value


def _extract_metric_score(
    case_res: EvaluationCaseResult, etype: EvaluatorType
) -> float | None:
    for m in case_res.metrics.values():
        if (
            m.evaluator_type == etype
            and m.score is not None
            and m.status in (MetricStatus.SUCCESS, MetricStatus.THRESHOLD_FAILED)
        ):
            return m.score
    return None


def to_run_response(
    run: EvaluationRun, dataset_id: UUID | None = None
) -> EvaluationRunResponse:
    resolved_dataset_id = dataset_id
    if resolved_dataset_id is None and run.evaluation is not None:
        resolved_dataset_id = run.evaluation.dataset_id

    if resolved_dataset_id is None:
        resolved_dataset_id = uuid.UUID(int=0)

    return EvaluationRunResponse(
        id=run.id,
        evaluation_id=run.evaluation_id,
        dataset_id=resolved_dataset_id,
        dataset_version=run.dataset_version,
        dataset_snapshot_hash=run.dataset_snapshot_hash,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_ms=run.duration_ms,
        correlation_id=run.correlation_id,
        overall_score=run.overall_score,
        total_cases=run.total_cases,
        completed_cases=run.completed_cases,
        failed_cases=run.failed_cases,
        error_message=run.error_message,
        metrics_summary=run.metrics_summary,
        execution_metadata=run.execution_metadata,
        config_id=run.config_id,
        config_version=run.config_version,
        config_snapshot_hash=run.config_snapshot_hash,
        created_at=run.created_at,
    )


def to_result_response(result: EvaluationResult) -> EvaluationResultResponse:
    return EvaluationResultResponse(
        id=result.id,
        run_id=result.run_id,
        case_id=result.case_id,
        response=result.response,
        overall_score=result.overall_score,
        passed=result.passed,
        status=result.status,
        factuality_score=result.factuality_score,
        relevance_score=result.relevance_score,
        faithfulness_score=result.faithfulness_score,
        instruction_score=result.instruction_score,
        consistency_score=result.consistency_score,
        hallucination_score=result.hallucination_score,
        feedback=result.feedback,
        execution_time_ms=result.execution_time_ms,
        error_message=result.error_message,
        metrics=result.metrics,
        created_at=result.created_at,
    )


async def submit_evaluation_run(
    session: AsyncSession,
    data: EvaluationRunCreate,
    owner_user_id: UUID | None = None,
    correlation_id: str | None = None,
    observability: EvaluationObservability | None = None,
) -> EvaluationRun:
    """Submits and validates an evaluation run, creating the PENDING record.

    Validates bounds, checks dataset/cases, calculates snapshot hash,
    attaches correlation ID, and emits run_created event.
    """
    obs = observability or EvaluationObservability()
    cid = validate_and_normalize_correlation_id(correlation_id)

    # 1. Enforce safety bounds
    if len(data.evaluators) > MAX_EVALUATORS_PER_RUN:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Maximum evaluators per run is {MAX_EVALUATORS_PER_RUN}, "
                f"got {len(data.evaluators)}"
            ),
        )

    # Validate execution metadata bounds if provided
    exec_meta = None
    if data.execution_metadata is not None:
        try:
            exec_meta = validate_execution_metadata(data.execution_metadata)
        except ValueError as err:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(err),
            ) from err

    # 2. Validate Dataset and Cases exist and are accessible
    dataset_query = select(Dataset).where(Dataset.id == data.dataset_id)
    if owner_user_id is not None:
        dataset_query = dataset_query.where(
            or_(Dataset.owner_user_id == owner_user_id, Dataset.owner_user_id.is_(None))
        )
    dataset = await session.scalar(dataset_query)
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset '{data.dataset_id}' not found",
        )

    cases_stmt = (
        select(DatasetCase)
        .where(DatasetCase.dataset_id == data.dataset_id)
        .order_by(DatasetCase.id)
    )
    cases = (await session.scalars(cases_stmt)).all()
    if not cases:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Dataset '{data.dataset_id}' has no cases to evaluate",
        )

    if len(cases) > MAX_CASES_PER_RUN:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Maximum cases per synchronous run is {MAX_CASES_PER_RUN}, "
                f"got {len(cases)}"
            ),
        )

    # 3. Snapshot hash & version capture
    dataset_version = dataset.version
    snapshot_hash = compute_dataset_snapshot_hash(cases)

    # 4. Resolve or create Evaluation
    resolved_config_id: uuid.UUID | None = None
    resolved_config_version: int | None = None
    resolved_snapshot_hash: str | None = None
    resolved_snapshot_dict: dict[str, Any] | None = None
    cfg_entity: Any = None

    if data.config_id is not None:
        from app.models.evaluation_configuration import EvaluationConfigVersion
        from app.services import evaluation_config_service

        cfg_entity = await evaluation_config_service.get_configuration(
            session=session,
            config_id=data.config_id,
            owner_user_id=owner_user_id,
            version=data.config_version,
        )
        if cfg_entity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Evaluation configuration '{data.config_id}' not found",
            )
        resolved_config_id = (
            cfg_entity.config_id
            if isinstance(cfg_entity, EvaluationConfigVersion)
            else cfg_entity.id
        )
        resolved_config_version = cfg_entity.version
        resolved_snapshot_hash = cfg_entity.snapshot_hash
        resolved_snapshot_dict, _ = compute_configuration_snapshot(
            name=cfg_entity.name,
            description=cfg_entity.description,
            version=cfg_entity.version,
            evaluators=cfg_entity.evaluators,
        )

    evaluation: Evaluation
    if data.evaluation_id is not None:
        eval_query = select(Evaluation).where(Evaluation.id == data.evaluation_id)
        if owner_user_id is not None:
            eval_query = eval_query.join(
                Dataset, Evaluation.dataset_id == Dataset.id
            ).where(
                or_(Dataset.owner_user_id == owner_user_id, Dataset.owner_user_id.is_(None))
            )
        existing_eval = await session.scalar(eval_query)
        if existing_eval is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Evaluation '{data.evaluation_id}' not found",
            )
        evaluation = existing_eval
    else:
        eval_name = data.name or f"Evaluation for {dataset.name}"
        if cfg_entity is not None:
            default_name = f"Evaluation {cfg_entity.name} on {dataset.name}"
        else:
            default_name = f"Evaluation for {dataset.name}"
        eval_name = data.name or default_name
        evaluation = Evaluation(
            name=eval_name,
            model_provider=data.model_provider,
            model_name=data.model_name,
            system_prompt=data.system_prompt,
            dataset_id=data.dataset_id,
        )
        session.add(evaluation)
        await session.flush()

        for cfg in data.evaluators:
            cfg_dict = dict(cfg.configuration or {})
            cfg_dict["_backend"] = cfg.backend
            if cfg.threshold is not None:
                cfg_dict["_threshold"] = cfg.threshold
            if cfg.name is not None:
                cfg_dict["_name"] = cfg.name
            config_record = EvaluatorConfig(
                evaluation_id=evaluation.id,
                evaluator_type=cfg.evaluator_type,
                enabled=True,
                configuration=cfg_dict,
            )
            session.add(config_record)
        if cfg_entity is not None:
            for defn in cfg_entity.evaluators:
                if not defn.get("enabled", True):
                    continue
                cfg_dict = dict(defn.get("configuration") or {})
                cfg_dict["_backend"] = defn.get("backend", "llm_judge")
                if defn.get("threshold") is not None:
                    cfg_dict["_threshold"] = defn.get("threshold")
                if defn.get("name") is not None:
                    cfg_dict["_name"] = defn.get("name")
                if defn.get("weight") is not None:
                    cfg_dict["_weight"] = float(defn.get("weight", 1.0))
                config_record = EvaluatorConfig(
                    evaluation_id=evaluation.id,
                    evaluator_type=defn["evaluator_type"],
                    enabled=True,
                    configuration=cfg_dict,
                )
                session.add(config_record)
        else:
            for cfg in data.evaluators:
                cfg_dict = dict(cfg.configuration or {})
                cfg_dict["_backend"] = cfg.backend
                if cfg.threshold is not None:
                    cfg_dict["_threshold"] = cfg.threshold
                if cfg.name is not None:
                    cfg_dict["_name"] = cfg.name
                config_record = EvaluatorConfig(
                    evaluation_id=evaluation.id,
                    evaluator_type=cfg.evaluator_type,
                    enabled=True,
                    configuration=cfg_dict,
                )
                session.add(config_record)
        await session.flush()

    # Serialize candidate responses for run storage
    candidate_resps: list[dict[str, Any]] | None = None
    if data.responses:
        candidate_resps = []
        if isinstance(data.responses, list):
            for r in data.responses:
                if r.candidate_responses:
                    for cr in r.candidate_responses:
                        candidate_resps.append(
                            {"case_id": str(r.case_id), "response": cr}
                        )
                elif r.response:
                    candidate_resps.append(
                        {"case_id": str(r.case_id), "response": r.response}
                    )
        elif isinstance(data.responses, dict):
            for k, v in data.responses.items():
                if isinstance(v, list):
                    for cr in v:
                        candidate_resps.append({"case_id": str(k), "response": str(cr)})
                else:
                    candidate_resps.append({"case_id": str(k), "response": str(v)})

    # 5. Create EvaluationRun in PENDING state
    run_id = uuid.uuid4()
    run = EvaluationRun(
        id=run_id,
        evaluation_id=evaluation.id,
        status=RunStatus.PENDING.value,
        dataset_version=dataset_version,
        dataset_snapshot_hash=snapshot_hash,
        correlation_id=cid,
        execution_metadata=exec_meta,
        candidate_responses=candidate_resps,
        config_id=resolved_config_id,
        config_version=resolved_config_version,
        config_snapshot_hash=resolved_snapshot_hash,
        config_snapshot=resolved_snapshot_dict,
        total_cases=len(cases),
        completed_cases=0,
        failed_cases=0,
    )
    session.add(run)

    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.RUN_CREATED,
        correlation_id=cid,
        resource_type="run",
        resource_id=run_id,
        run_id=run_id,
        actor_user_id=dataset.owner_user_id,
        owner_user_id=dataset.owner_user_id,
        outcome="success",
        metadata={"dataset_id": str(data.dataset_id), "total_cases": len(cases)},
    )
    await session.commit()

    obs.emit(
        EvaluationEvent(
            event_type=EvaluationEventType.RUN_CREATED,
            run_id=run_id,
            dataset_id=data.dataset_id,
            correlation_id=cid,
            status=RunStatus.PENDING.value,
            details={"total_cases": len(cases)},
        )
    )

    return run


async def execute_evaluation_run(
    session: AsyncSession,
    run_id: UUID,
    data: EvaluationRunCreate | None = None,
    correlation_id: str | None = None,
    evaluator_overrides: Sequence[BaseEvaluator] | None = None,
    observability: EvaluationObservability | None = None,
    worker_id: str | None = None,
    job_id: str | None = None,
    attempt: int | None = None,
) -> EvaluationRun:
    """Executes a previously submitted run through the evaluation engine."""
    obs = observability or EvaluationObservability()
    start_time = time.perf_counter()

    # 1. Authoritative Atomic Claim: PENDING -> RUNNING
    from sqlalchemy import update

    claim_stmt = (
        update(EvaluationRun)
        .where(
            EvaluationRun.id == run_id,
            EvaluationRun.status == RunStatus.PENDING.value,
        )
        .values(
            status=RunStatus.RUNNING.value,
            started_at=datetime.now(UTC),
        )
    )
    claim_res = await session.execute(claim_stmt)
    await session.commit()

    run = await session.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EvaluationRun '{run_id}' not found",
        )

    cid = run.correlation_id or validate_and_normalize_correlation_id(correlation_id)

    # If 0 rows updated, delivery is duplicate or run is already terminal
    claim_count = getattr(claim_res, "rowcount", 0)
    if claim_count == 0:
        logger.warning(
            "Run %s is not PENDING (current: %s). Skipping duplicate delivery.",
            run_id,
            run.status,
        )
        return run

    # Load associated Evaluation record
    evaluation = await session.get(Evaluation, run.evaluation_id)
    if evaluation is None:
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        await _record_fatal_run_failure(
            session, run_id, "Evaluation definition not found", duration_ms=elapsed_ms
        )
        obs.emit(
            EvaluationEvent(
                event_type=EvaluationEventType.RUN_FAILED,
                run_id=run_id,
                correlation_id=cid,
                status=RunStatus.FAILED.value,
                duration_ms=elapsed_ms,
                error="Evaluation definition not found",
                worker_id=worker_id,
                job_id=job_id,
                attempt=attempt,
            )
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Evaluation definition not found",
        )

    target_dataset_id = data.dataset_id if data else evaluation.dataset_id
    target_system_prompt = data.system_prompt if data else evaluation.system_prompt
    dataset_rec = await session.get(Dataset, target_dataset_id)
    owner_user_id = dataset_rec.owner_user_id if dataset_rec else None

    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.RUN_STARTED,
        correlation_id=cid,
        resource_type="run",
        resource_id=run_id,
        run_id=run_id,
        owner_user_id=owner_user_id,
        outcome="success",
    )
    await session.commit()

    obs.emit(
        EvaluationEvent(
            event_type=EvaluationEventType.RUN_STARTED,
            run_id=run_id,
            dataset_id=target_dataset_id,
            correlation_id=cid,
            status=RunStatus.RUNNING.value,
            worker_id=worker_id,
            job_id=job_id,
            attempt=attempt,
        )
    )

    # 2. Instantiate and validate evaluators
    evaluators: list[BaseEvaluator] = []
    weights_map: dict[str, float] = {}

    if run.config_snapshot and isinstance(run.config_snapshot, dict):
        for item in run.config_snapshot.get("evaluators", []):
            ename = item.get("name") or item.get("evaluator_type")
            if ename and "weight" in item and item["weight"] is not None:
                weights_map[str(ename)] = float(item["weight"])

    if evaluator_overrides is not None:
        evaluators = list(evaluator_overrides)
    elif data and data.evaluators:
        for cfg in data.evaluators:
            try:
                evaluator = create_evaluator(
                    evaluator_type=cfg.evaluator_type,
                    backend=cfg.backend,
                    name=cfg.name,
                    threshold=cfg.threshold,
                    **(cfg.configuration or {}),
                )
                evaluators.append(evaluator)
                if cfg.weight is not None:
                    weights_map[evaluator.name] = float(cfg.weight)
            except UnsupportedEvaluatorError as err:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                await _record_fatal_run_failure(
                    session, run_id, str(err.message), duration_ms=elapsed_ms
                )
                obs.emit(
                    EvaluationEvent(
                        event_type=EvaluationEventType.RUN_FAILED,
                        run_id=run_id,
                        dataset_id=target_dataset_id,
                        correlation_id=cid,
                        status=RunStatus.FAILED.value,
                        duration_ms=elapsed_ms,
                        error=str(err.message),
                        worker_id=worker_id,
                        job_id=job_id,
                        attempt=attempt,
                    )
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid evaluator configuration: {err.message}",
                ) from err
            except Exception as err:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                await _record_fatal_run_failure(
                    session, run_id, str(err), duration_ms=elapsed_ms
                )
                obs.emit(
                    EvaluationEvent(
                        event_type=EvaluationEventType.RUN_FAILED,
                        run_id=run_id,
                        dataset_id=target_dataset_id,
                        correlation_id=cid,
                        status=RunStatus.FAILED.value,
                        duration_ms=elapsed_ms,
                        error=str(err),
                        worker_id=worker_id,
                        job_id=job_id,
                        attempt=attempt,
                    )
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Failed to configure evaluator '{cfg.evaluator_type}': {err}"
                    ),
                ) from err
    else:
        # Load from database evaluator configs
        cfg_stmt = select(EvaluatorConfig).where(
            EvaluatorConfig.evaluation_id == evaluation.id,
            EvaluatorConfig.enabled.is_(True),
        )
        db_configs = (await session.scalars(cfg_stmt)).all()
        for db_cfg in db_configs:
            cfg_dict = dict(db_cfg.configuration or {})
            backend = str(cfg_dict.pop("_backend", "native"))
            thresh_val = cfg_dict.pop("_threshold", None)
            threshold = float(thresh_val) if thresh_val is not None else None
            name = cfg_dict.pop("_name", None)
            weight_val = cfg_dict.pop("_weight", None)
            weight = float(weight_val) if weight_val is not None else 1.0
            try:
                evaluator = create_evaluator(
                    evaluator_type=db_cfg.evaluator_type,
                    backend=backend,
                    name=name,
                    threshold=threshold,
                    **cfg_dict,
                )
                evaluators.append(evaluator)
                weights_map[evaluator.name] = weight
            except UnsupportedEvaluatorError as err:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                await _record_fatal_run_failure(
                    session, run_id, str(err.message), duration_ms=elapsed_ms
                )
                obs.emit(
                    EvaluationEvent(
                        event_type=EvaluationEventType.RUN_FAILED,
                        run_id=run_id,
                        dataset_id=target_dataset_id,
                        correlation_id=cid,
                        status=RunStatus.FAILED.value,
                        duration_ms=elapsed_ms,
                        error=str(err.message),
                        worker_id=worker_id,
                        job_id=job_id,
                        attempt=attempt,
                    )
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid evaluator configuration: {err.message}",
                ) from err
            except Exception as err:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                await _record_fatal_run_failure(
                    session, run_id, str(err), duration_ms=elapsed_ms
                )
                obs.emit(
                    EvaluationEvent(
                        event_type=EvaluationEventType.RUN_FAILED,
                        run_id=run_id,
                        dataset_id=target_dataset_id,
                        correlation_id=cid,
                        status=RunStatus.FAILED.value,
                        duration_ms=elapsed_ms,
                        error=str(err),
                        worker_id=worker_id,
                        job_id=job_id,
                        attempt=attempt,
                    )
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Failed to configure evaluator "
                        f"'{db_cfg.evaluator_type}': {err}"
                    ),
                ) from err

    # 3. Load cases & candidate responses
    cases_stmt = (
        select(DatasetCase)
        .where(DatasetCase.dataset_id == target_dataset_id)
        .order_by(DatasetCase.id)
    )
    cases = (await session.scalars(cases_stmt)).all()

    multi_response_map: dict[UUID, list[str]] = {}
    if data and data.responses:
        if isinstance(data.responses, dict):
            for k, v in data.responses.items():
                if isinstance(v, list):
                    for cr in v:
                        multi_response_map.setdefault(k, []).append(str(cr))
                else:
                    multi_response_map.setdefault(k, []).append(str(v))
        elif isinstance(data.responses, list):
            for item in data.responses:
                if isinstance(item, CaseResponseInput):
                    if item.candidate_responses:
                        for cr in item.candidate_responses:
                            multi_response_map.setdefault(item.case_id, []).append(cr)
                    elif item.response:
                        multi_response_map.setdefault(item.case_id, []).append(
                            item.response
                        )
                elif isinstance(item, dict):
                    c_id = UUID(str(item["case_id"]))
                    cand_list = item.get("candidate_responses")
                    if isinstance(cand_list, list):
                        for cr in cand_list:
                            multi_response_map.setdefault(c_id, []).append(str(cr))
                    elif "response" in item and item["response"]:
                        multi_response_map.setdefault(c_id, []).append(
                            str(item["response"])
                        )
    elif run.candidate_responses:
        for saved_item in run.candidate_responses:
            if isinstance(saved_item, dict):
                c_id = UUID(str(saved_item["case_id"]))
                cand_val = saved_item.get("response")
                if cand_val is not None:
                    multi_response_map.setdefault(c_id, []).append(str(cand_val))

    eval_inputs: list[EvaluationInput] = []
    for case in cases:
        case_meta = case.metadata_
        cands = multi_response_map.get(case.id)
        if not cands and case_meta and isinstance(case_meta, dict):
            meta_resps = case_meta.get("candidate_responses") or case_meta.get(
                "responses"
            )
            if isinstance(meta_resps, list):
                cands = [str(r) for r in meta_resps]
            else:
                single_resp = case_meta.get("response") or case_meta.get(
                    "actual_output"
                )
                if single_resp:
                    cands = [str(single_resp)]

        if not cands:
            candidate_resp = (
                case.expected_output
                or f"Candidate response for prompt: {case.input[:50]}"
            )
            cands = [candidate_resp]
        else:
            candidate_resp = cands[0]

        has_multi = len(cands) >= 2
        ctx = EvaluationContext(
            input=case.input,
            expected_output=case.expected_output,
            context=case.context,
            metadata=case_meta,
            system_prompt=target_system_prompt,
            candidate_responses=cands if has_multi else None,
        )
        eval_inputs.append(
            EvaluationInput(
                case_id=case.id,
                response=candidate_resp,
                candidate_responses=cands if has_multi else None,
                context=ctx,
            )
        )

        obs.emit(
            EvaluationEvent(
                event_type=EvaluationEventType.CASE_EVALUATION_STARTED,
                run_id=run_id,
                dataset_id=target_dataset_id,
                case_id=case.id,
                correlation_id=cid,
                status="evaluating",
                worker_id=worker_id,
                job_id=job_id,
                attempt=attempt,
            )
        )

    # 4. Execute evaluation run outside DB transaction
    from app.evaluation.engine import EvaluationEngine

    engine = EvaluationEngine()
    run_result: EvaluationRunResult
    try:
        run_result = await engine.evaluate_run(
            inputs=eval_inputs,
            evaluators=evaluators,
            weights=weights_map or None,
            run_id=run_id,
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        logger.exception("Fatal evaluator execution error in run %s", run_id)
        await _record_fatal_run_failure(
            session, run_id, str(exc), duration_ms=elapsed_ms
        )
        obs.emit(
            EvaluationEvent(
                event_type=EvaluationEventType.RUN_FAILED,
                run_id=run_id,
                dataset_id=target_dataset_id,
                correlation_id=cid,
                status=RunStatus.FAILED.value,
                duration_ms=elapsed_ms,
                error=str(exc),
                worker_id=worker_id,
                job_id=job_id,
                attempt=attempt,
            )
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Fatal evaluation execution failure: "
                f"{_sanitize_error_message(str(exc))}"
            ),
        ) from exc

    # 5. Short DB Write: Atomic Result Persistence
    try:
        for case_res in run_result.case_results:
            case_status = _determine_case_status(case_res)

            fact_score = _extract_metric_score(case_res, EvaluatorType.FACTUALITY)
            rel_score = _extract_metric_score(case_res, EvaluatorType.RELEVANCE)
            faith_score = _extract_metric_score(case_res, EvaluatorType.FAITHFULNESS)
            inst_score = _extract_metric_score(
                case_res, EvaluatorType.INSTRUCTION_FOLLOWING
            )
            cons_score = _extract_metric_score(case_res, EvaluatorType.CONSISTENCY)
            hall_score = _extract_metric_score(case_res, EvaluatorType.HALLUCINATION)

            explanations = [
                f"[{m.metric_name}] {m.explanation}"
                for m in case_res.metrics.values()
                if m.explanation
            ]
            feedback_str = "\n".join(explanations) if explanations else None

            metrics_dump = {
                k: m.model_dump(mode="json") for k, m in case_res.metrics.items()
            }

            db_result = EvaluationResult(
                id=uuid.uuid4(),
                run_id=run_id,
                case_id=case_res.case_id,
                response=case_res.response,
                overall_score=case_res.overall_score,
                passed=case_res.passed,
                status=case_status,
                factuality_score=fact_score,
                relevance_score=rel_score,
                faithfulness_score=faith_score,
                instruction_score=inst_score,
                consistency_score=cons_score,
                hallucination_score=hall_score,
                feedback=feedback_str,
                execution_time_ms=case_res.execution_time_ms,
                metrics=metrics_dump,
                error_message="; ".join(case_res.errors) if case_res.errors else None,
            )
            session.add(db_result)

            obs.emit(
                EvaluationEvent(
                    event_type=EvaluationEventType.CASE_EVALUATION_COMPLETED,
                    run_id=run_id,
                    dataset_id=target_dataset_id,
                    case_id=case_res.case_id,
                    correlation_id=cid,
                    status=case_status,
                    duration_ms=case_res.execution_time_ms,
                    score=case_res.overall_score,
                    worker_id=worker_id,
                    job_id=job_id,
                    attempt=attempt,
                )
            )

        run_record = await session.get(EvaluationRun, run_id)
        if run_record is None:
            raise RuntimeError(f"Run {run_id} disappeared during persistence")

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        if run_record.status == RunStatus.CANCELLED.value:
            logger.info(
                "Run %s was cancelled during execution. Preserving CANCELLED status.",
                run_id,
            )
            run_record.completed_cases = run_result.completed_cases
            run_record.failed_cases = run_result.failed_cases
            await session.commit()
            return run_record

        run_record.status = validate_run_transition(
            run_record.status, RunStatus.COMPLETED
        ).value
        run_record.completed_at = datetime.now(UTC)
        run_record.duration_ms = elapsed_ms
        run_record.overall_score = run_result.overall_score
        run_record.completed_cases = run_result.completed_cases
        run_record.failed_cases = run_result.failed_cases
        run_record.metrics_summary = {
            "metric_averages": run_result.metric_averages,
            "execution_time_ms": run_result.execution_time_ms,
            "evaluators": [e.name for e in evaluators],
        }

        await audit_service.record_audit_event(
            session=session,
            event_type=EvaluationEventType.RUN_COMPLETED,
            correlation_id=cid,
            resource_type="run",
            resource_id=run_id,
            run_id=run_id,
            owner_user_id=owner_user_id,
            outcome="success",
            duration_ms=elapsed_ms,
            metadata={
                "total_cases": run_record.total_cases,
                "completed_cases": run_record.completed_cases,
                "failed_cases": run_record.failed_cases,
                "overall_score": run_record.overall_score,
            },
        )
        await session.commit()

        obs.emit(
            EvaluationEvent(
                event_type=EvaluationEventType.RUN_COMPLETED,
                run_id=run_id,
                dataset_id=target_dataset_id,
                correlation_id=cid,
                status=RunStatus.COMPLETED.value,
                duration_ms=elapsed_ms,
                score=run_record.overall_score,
                worker_id=worker_id,
                job_id=job_id,
                attempt=attempt,
                details={
                    "total_cases": run_record.total_cases,
                    "completed_cases": run_record.completed_cases,
                    "failed_cases": run_record.failed_cases,
                },
            )
        )

        return run_record

    except HTTPException:
        raise
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        logger.exception("Persistence error during run %s: %s", run_id, exc)
        await _record_fatal_run_failure(
            session, run_id, str(exc), duration_ms=elapsed_ms
        )
        try:
            await audit_service.record_audit_event(
                session=session,
                event_type=EvaluationEventType.RUN_FAILED,
                correlation_id=cid,
                resource_type="run",
                resource_id=run_id,
                run_id=run_id,
                owner_user_id=owner_user_id,
                outcome="failure",
                duration_ms=elapsed_ms,
                metadata={"error": str(exc)},
            )
            await session.commit()
        except Exception:
            pass
        obs.emit(
            EvaluationEvent(
                event_type=EvaluationEventType.RUN_FAILED,
                run_id=run_id,
                dataset_id=target_dataset_id,
                correlation_id=cid,
                status=RunStatus.FAILED.value,
                duration_ms=elapsed_ms,
                error=str(exc),
                worker_id=worker_id,
                job_id=job_id,
                attempt=attempt,
            )
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Persistence failed for evaluation run: "
                f"{_sanitize_error_message(str(exc))}"
            ),
        ) from exc


async def create_and_execute_evaluation_run(
    session: AsyncSession,
    data: EvaluationRunCreate,
    correlation_id: str | None = None,
    evaluator_overrides: Sequence[BaseEvaluator] | None = None,
    observability: EvaluationObservability | None = None,
) -> EvaluationRun:
    """Synchronously coordinates run submission and execution for Step 05."""
    obs = observability or EvaluationObservability()
    from app.services.run_executor import SynchronousRunExecutor

    executor = SynchronousRunExecutor(observability=obs)
    return await executor.submit_and_execute(
        session=session,
        data=data,
        correlation_id=correlation_id,
        evaluator_overrides=evaluator_overrides,
    )


async def _record_fatal_run_failure(
    session: AsyncSession,
    run_id: UUID,
    error_text: str,
    duration_ms: float | None = None,
) -> None:
    """Safely transitions an EvaluationRun to FAILED, ensuring atomic containment."""
    try:
        run = await session.get(EvaluationRun, run_id)
        if run is not None:
            run.status = RunStatus.FAILED.value
            run.completed_at = datetime.now(UTC)
            if duration_ms is not None:
                run.duration_ms = duration_ms
            run.error_message = _sanitize_error_message(error_text)
            await session.commit()
    except Exception as exc:
        logger.exception("Failed to record fatal run failure state: %s", exc)


async def get_evaluation_run(
    session: AsyncSession,
    run_id: UUID,
    owner_user_id: UUID | None = None,
) -> EvaluationRun | None:
    """Retrieves an EvaluationRun with Evaluation, optionally scoped by owner."""
    stmt = select(EvaluationRun).options(selectinload(EvaluationRun.evaluation))
    if owner_user_id is not None:
        stmt = (
            stmt.join(EvaluationRun.evaluation)
            .join(Evaluation.dataset)
            .where(
                EvaluationRun.id == run_id,
                or_(
                    Dataset.owner_user_id == owner_user_id,
                    Dataset.owner_user_id.is_(None),
                ),
            )
        )
    else:
        stmt = stmt.where(EvaluationRun.id == run_id)
    return await session.scalar(stmt)


async def list_evaluation_runs(
    session: AsyncSession,
    owner_user_id: UUID | None = None,
    dataset_id: UUID | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[EvaluationRun], int]:
    """Retrieves a paginated list of evaluation runs with total count."""
    base_query = select(EvaluationRun).join(EvaluationRun.evaluation)
    if owner_user_id is not None:
        base_query = base_query.join(Evaluation.dataset).where(
            or_(
                Dataset.owner_user_id == owner_user_id,
                Dataset.owner_user_id.is_(None),
            )
        )

    if dataset_id is not None:
        base_query = base_query.where(Evaluation.dataset_id == dataset_id)
    if status_filter is not None:
        base_query = base_query.where(EvaluationRun.status == status_filter.lower())

    count_query = select(func.count()).select_from(base_query.subquery())
    total = await session.scalar(count_query) or 0

    stmt = (
        base_query.options(selectinload(EvaluationRun.evaluation))
        .order_by(EvaluationRun.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    runs = (await session.scalars(stmt)).all()
    return list(runs), total


async def get_evaluation_run_results(
    session: AsyncSession,
    run_id: UUID,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[EvaluationResult], int]:
    """Retrieves paginated EvaluationResult rows for a run."""
    count_query = (
        select(func.count())
        .select_from(EvaluationResult)
        .where(EvaluationResult.run_id == run_id)
    )
    total = await session.scalar(count_query) or 0

    stmt = (
        select(EvaluationResult)
        .where(EvaluationResult.run_id == run_id)
        .order_by(EvaluationResult.created_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    results = (await session.scalars(stmt)).all()
    return list(results), total


async def cancel_evaluation_run(
    session: AsyncSession,
    run_id: UUID,
    owner_user_id: UUID,
    correlation_id: str | None = None,
) -> EvaluationRun:
    """Cancels an in-flight or pending evaluation run.

    Scoped strictly to the dataset owner (raises 404 if not found or unauthorized).
    Raises HTTPException(409) if the run is already in a terminal state.
    """
    stmt = (
        select(EvaluationRun)
        .options(
            selectinload(EvaluationRun.evaluation).selectinload(Evaluation.dataset)
        )
        .join(EvaluationRun.evaluation)
        .join(Evaluation.dataset)
        .where(
            EvaluationRun.id == run_id,
            Dataset.owner_user_id == owner_user_id,
        )
    )
    run = await session.scalar(stmt)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evaluation run not found",
        )

    terminal_statuses = {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    }
    if run.status in terminal_statuses:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel evaluation run in terminal state '{run.status}'",
        )

    prev_status = run.status
    target_status = validate_run_transition(run.status, RunStatus.CANCELLED)
    run.status = target_status.value
    run.completed_at = datetime.now(UTC)

    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.RUN_CANCELLED,
        correlation_id=correlation_id or run.correlation_id or "evalx-cancel",
        resource_type="run",
        resource_id=run.id,
        run_id=run.id,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        outcome="cancelled",
        metadata={"previous_status": prev_status},
    )
    await session.commit()
    await session.refresh(run)

    obs = EvaluationObservability()
    obs.emit(
        EvaluationEvent(
            event_type=EvaluationEventType.RUN_CANCELLED,
            run_id=run.id,
            dataset_id=run.evaluation.dataset_id if run.evaluation else None,
            correlation_id=correlation_id or run.correlation_id,
            status=RunStatus.CANCELLED.value,
            details={"previous_status": prev_status},
        )
    )
    return run


async def reap_stale_runs(
    session: AsyncSession,
    timeout_seconds: int = 1800,
    batch_size: int = 50,
) -> list[UUID]:
    """Identifies running runs older than timeout threshold and marks them FAILED."""
    cutoff = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
    stmt = (
        select(EvaluationRun)
        .where(
            EvaluationRun.status == RunStatus.RUNNING.value,
            EvaluationRun.started_at <= cutoff,
        )
        .limit(batch_size)
    )
    stale_runs = (await session.scalars(stmt)).all()
    reaped_ids: list[UUID] = []

    obs = EvaluationObservability()
    for run in stale_runs:
        run.status = RunStatus.FAILED.value
        run.completed_at = datetime.now(UTC)
        run.error_message = (
            f"Evaluation run timed out after exceeding duration threshold of "
            f"{timeout_seconds}s"
        )
        reaped_ids.append(run.id)
        await audit_service.record_audit_event(
            session=session,
            event_type=EvaluationEventType.RUN_TIMED_OUT,
            correlation_id=run.correlation_id or "evalx-timeout",
            resource_type="run",
            resource_id=run.id,
            run_id=run.id,
            outcome="timeout",
            metadata={"error": run.error_message},
        )
        obs.emit(
            EvaluationEvent(
                event_type=EvaluationEventType.RUN_TIMED_OUT,
                run_id=run.id,
                correlation_id=run.correlation_id,
                status=RunStatus.FAILED.value,
                error=run.error_message,
            )
        )

    if reaped_ids:
        await session.commit()
        logger.warning(
            "Reaped %d stale evaluation runs: %s",
            len(reaped_ids),
            [str(i) for i in reaped_ids],
        )

    return reaped_ids


async def compare_evaluation_runs(
    session: AsyncSession,
    base_run_id: UUID,
    target_run_id: UUID,
    owner_user_id: UUID,
    threshold: float = 0.0,
) -> RunComparisonResponse:
    """Compares completed runs for regression and improvement analytics."""
    base_run = await get_evaluation_run(
        session, base_run_id, owner_user_id=owner_user_id
    )
    if base_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Base evaluation run not found",
        )

    target_run = await get_evaluation_run(
        session, target_run_id, owner_user_id=owner_user_id
    )
    if target_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target evaluation run not found",
        )

    terminal_statuses = {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    }
    if base_run.status not in terminal_statuses:
        detail_msg = (
            f"Base run {base_run_id} is still in progress (status: {base_run.status})"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail_msg,
        )
    if target_run.status not in terminal_statuses:
        detail_msg = (
            f"Target run {target_run_id} is still in progress "
            f"(status: {target_run.status})"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail_msg,
        )

    stmt_base = select(EvaluationResult).where(EvaluationResult.run_id == base_run_id)
    base_results_list = (await session.scalars(stmt_base)).all()
    base_results = {r.case_id: r for r in base_results_list}

    stmt_target = select(EvaluationResult).where(
        EvaluationResult.run_id == target_run_id
    )
    target_results_list = (await session.scalars(stmt_target)).all()
    target_results = {r.case_id: r for r in target_results_list}

    all_case_ids = sorted(
        set(base_results.keys()) | set(target_results.keys()),
        key=lambda u: str(u),
    )

    case_comparisons: list[CaseComparison] = []
    regressions_count = 0
    improvements_count = 0
    unchanged_count = 0
    added_cases_count = 0
    removed_cases_count = 0

    for case_id in all_case_ids:
        b_res = base_results.get(case_id)
        t_res = target_results.get(case_id)

        if b_res is None and t_res is not None:
            added_cases_count += 1
            case_comparisons.append(
                CaseComparison(
                    case_id=case_id,
                    category=CaseComparisonCategory.ADDED,
                    target_score=t_res.overall_score,
                    target_passed=t_res.passed,
                )
            )
        elif b_res is not None and t_res is None:
            removed_cases_count += 1
            case_comparisons.append(
                CaseComparison(
                    case_id=case_id,
                    category=CaseComparisonCategory.REMOVED,
                    base_score=b_res.overall_score,
                    base_passed=b_res.passed,
                )
            )
        elif b_res is not None and t_res is not None:
            score_delta: float | None = None
            if t_res.overall_score is not None and b_res.overall_score is not None:
                score_delta = round(t_res.overall_score - b_res.overall_score, 4)

            b_metrics = b_res.metrics or {}
            t_metrics = t_res.metrics or {}
            all_metric_keys = sorted(set(b_metrics.keys()) | set(t_metrics.keys()))
            case_metrics: dict[str, MetricComparison] = {}

            for m_key in all_metric_keys:
                b_m = b_metrics.get(m_key, {})
                t_m = t_metrics.get(m_key, {})
                b_m_score = b_m.get("score") if isinstance(b_m, dict) else None
                t_m_score = t_m.get("score") if isinstance(t_m, dict) else None
                m_delta = (
                    round(t_m_score - b_m_score, 4)
                    if (t_m_score is not None and b_m_score is not None)
                    else None
                )
                case_metrics[m_key] = MetricComparison(
                    metric_name=m_key,
                    base_score=b_m_score,
                    target_score=t_m_score,
                    delta=m_delta,
                    improved=(m_delta > threshold) if m_delta is not None else False,
                    regressed=(m_delta < -threshold) if m_delta is not None else False,
                )

            if score_delta is not None:
                if score_delta < -threshold or (
                    b_res.passed is True and t_res.passed is False
                ):
                    category = CaseComparisonCategory.REGRESSION
                    regressions_count += 1
                elif score_delta > threshold or (
                    b_res.passed is False and t_res.passed is True
                ):
                    category = CaseComparisonCategory.IMPROVEMENT
                    improvements_count += 1
                else:
                    category = CaseComparisonCategory.UNCHANGED
                    unchanged_count += 1
            else:
                if b_res.passed is True and t_res.passed is False:
                    category = CaseComparisonCategory.REGRESSION
                    regressions_count += 1
                elif b_res.passed is False and t_res.passed is True:
                    category = CaseComparisonCategory.IMPROVEMENT
                    improvements_count += 1
                else:
                    category = CaseComparisonCategory.UNCHANGED
                    unchanged_count += 1

            case_comparisons.append(
                CaseComparison(
                    case_id=case_id,
                    category=category,
                    base_score=b_res.overall_score,
                    target_score=t_res.overall_score,
                    delta=score_delta,
                    base_passed=b_res.passed,
                    target_passed=t_res.passed,
                    metrics=case_metrics,
                )
            )

    overall_delta: float | None = None
    if target_run.overall_score is not None and base_run.overall_score is not None:
        overall_delta = round(target_run.overall_score - base_run.overall_score, 4)

    base_summary = base_run.metrics_summary or {}
    target_summary = target_run.metrics_summary or {}
    base_avgs = base_summary.get("metric_averages", {})
    target_avgs = target_summary.get("metric_averages", {})
    all_summary_metrics = sorted(set(base_avgs.keys()) | set(target_avgs.keys()))
    metric_comparisons: dict[str, MetricComparison] = {}

    for m_name in all_summary_metrics:
        b_avg = base_avgs.get(m_name)
        t_avg = target_avgs.get(m_name)
        m_diff = (
            round(t_avg - b_avg, 4)
            if (t_avg is not None and b_avg is not None)
            else None
        )
        metric_comparisons[m_name] = MetricComparison(
            metric_name=m_name,
            base_score=b_avg,
            target_score=t_avg,
            delta=m_diff,
            improved=(m_diff > threshold) if m_diff is not None else False,
            regressed=(m_diff < -threshold) if m_diff is not None else False,
        )

    summary = RunComparisonSummary(
        base_run_id=base_run_id,
        target_run_id=target_run_id,
        base_overall_score=base_run.overall_score,
        target_overall_score=target_run.overall_score,
        overall_score_delta=overall_delta,
        base_dataset_version=base_run.dataset_version,
        target_dataset_version=target_run.dataset_version,
        base_snapshot_hash=base_run.dataset_snapshot_hash,
        target_snapshot_hash=target_run.dataset_snapshot_hash,
        total_cases_compared=len(all_case_ids),
        regressions_count=regressions_count,
        improvements_count=improvements_count,
        unchanged_count=unchanged_count,
        added_cases_count=added_cases_count,
        removed_cases_count=removed_cases_count,
        metric_comparisons=metric_comparisons,
    )

    return RunComparisonResponse(
        summary=summary,
        case_comparisons=case_comparisons,
    )
