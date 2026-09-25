import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from app.evaluation.contracts import BaseEvaluator
from app.evaluation.enums import EvaluatorType, MetricStatus
from app.evaluation.errors import InvalidEvaluationInputError
from app.evaluation.registry import EvaluatorRegistry
from app.evaluation.scoring import calculate_case_score, calculate_run_score
from app.evaluation.types import (
    EvaluationCaseResult,
    EvaluationInput,
    EvaluationMetric,
    EvaluationRunResult,
)


class EvaluationEngine:
    """Core provider-agnostic orchestrator for executing evaluations and scoring.

    Separation of concerns:
      PROMPTLAB: Intent -> Prompt
      EVALX: AI Response -> Measurement
    """

    def __init__(self, registry: EvaluatorRegistry | None = None) -> None:
        self._registry: EvaluatorRegistry = (
            registry if registry is not None else EvaluatorRegistry()
        )

    @property
    def registry(self) -> EvaluatorRegistry:
        """The underlying evaluator registry."""
        return self._registry

    def register_evaluator(self, evaluator: BaseEvaluator) -> None:
        """Register an evaluator instance with the engine."""
        self._registry.register(evaluator)

    def unregister_evaluator(self, name: str) -> None:
        """Remove a registered evaluator by name."""
        self._registry.unregister(name)

    def get_evaluator(self, name: str) -> BaseEvaluator:
        """Retrieve a registered evaluator by name."""
        return self._registry.get(name)

    def get_evaluators_by_type(
        self, evaluator_type: EvaluatorType | str
    ) -> list[BaseEvaluator]:
        """Retrieve all registered evaluators matching an EvaluatorType."""
        return self._registry.get_by_type(evaluator_type)

    def list_evaluators(self) -> list[str]:
        """Return the names of all registered evaluators."""
        return self._registry.list_evaluators()

    async def evaluate_case(
        self,
        input_data: EvaluationInput,
        evaluators: Sequence[BaseEvaluator] | None = None,
        weights: dict[str, float] | None = None,
    ) -> EvaluationCaseResult:
        """Evaluate a single test case across the specified evaluators.

        If evaluators is None, all registered evaluators are executed.
        Individual evaluator failures are contained so other evaluators continue.
        """
        if not isinstance(input_data, EvaluationInput):
            raise InvalidEvaluationInputError(
                "input_data must be an instance of EvaluationInput"
            )

        start_time = time.perf_counter()
        eval_list = (
            list(evaluators)
            if evaluators is not None
            else list(self._registry.values())
        )
        metrics: dict[str, EvaluationMetric] = {}
        errors: list[str] = []

        for evaluator in eval_list:
            try:
                metric = await evaluator.evaluate(input_data)
                metrics[metric.metric_name] = metric
            except Exception as err:
                error_msg = f"Evaluator '{evaluator.name}' failed: {err}"
                errors.append(error_msg)
                metrics[evaluator.name] = EvaluationMetric(
                    metric_name=evaluator.name,
                    evaluator_type=evaluator.evaluator_type,
                    score=None,
                    passed=False,
                    threshold=evaluator.threshold,
                    explanation=error_msg,
                    status=MetricStatus.ERROR,
                )

        overall_score, case_passed = calculate_case_score(metrics, weights=weights)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        return EvaluationCaseResult(
            case_id=input_data.case_id,
            response=input_data.response,
            metrics=metrics,
            overall_score=overall_score,
            passed=case_passed,
            errors=errors,
            execution_time_ms=elapsed_ms,
        )

    async def evaluate_run(
        self,
        inputs: Sequence[EvaluationInput],
        evaluators: Sequence[BaseEvaluator] | None = None,
        weights: dict[str, float] | None = None,
        run_id: UUID | None = None,
        cancellation_check: (
            Callable[[], Awaitable[bool]] | Callable[[], bool] | None
        ) = None,
    ) -> EvaluationRunResult:
        """Evaluate a batch of cases representing an entire EvaluationRun."""
        start_time = time.perf_counter()
        case_results: list[EvaluationCaseResult] = []

        for case_input in inputs:
            if cancellation_check is not None:
                is_cancelled = cancellation_check()
                if asyncio.iscoroutine(is_cancelled):
                    is_cancelled = await is_cancelled
                if is_cancelled:
                    break

            result = await self.evaluate_case(
                input_data=case_input,
                evaluators=evaluators,
                weights=weights,
            )
            case_results.append(result)

        overall_score, metric_averages, completed_count, failed_count = (
            calculate_run_score(case_results)
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        return EvaluationRunResult(
            run_id=run_id,
            total_cases=len(inputs),
            completed_cases=completed_count,
            failed_cases=failed_count,
            overall_score=overall_score,
            metric_averages=metric_averages,
            case_results=case_results,
            execution_time_ms=elapsed_ms,
        )
