import time
from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

from app.evaluation.enums import EvaluatorType, MetricStatus
from app.evaluation.errors import EvaluationError, EvaluatorExecutionError
from app.evaluation.types import EvaluationInput, EvaluationMetric


@runtime_checkable
class BaseEvaluator(Protocol):
    """Clean protocol interface for all EVALX evaluators.

    An evaluator accepts an EvaluationInput and asynchronously returns an
    EvaluationMetric without coupling to an evaluation algorithm or LLM provider.

    Designed to support:
      - Deterministic evaluators (exact match, regex, keyword presence)
      - Heuristic evaluators (word overlap, length ratio, token count)
      - LLM-as-a-Judge evaluators (factuality, relevance, instruction following)
      - Future RAG evaluators (faithfulness, hallucination, retrieval precision)
    """

    @property
    def name(self) -> str:
        """Unique identifier or name for this evaluator instance."""
        ...

    @property
    def evaluator_type(self) -> EvaluatorType:
        """The core category of this evaluator."""
        ...

    @property
    def threshold(self) -> float | None:
        """Explicit passing threshold in range [0.0, 1.0], or None if unconfigured."""
        ...

    async def evaluate(self, input_data: EvaluationInput) -> EvaluationMetric:
        """Evaluate a single EvaluationInput and return an EvaluationMetric."""
        ...


class AbstractEvaluator(ABC, BaseEvaluator):
    """Convenience base class for evaluators.

    Provides standardized timing, threshold verification, and error containment.
    Concrete subclasses only need to implement `_compute_score`.
    """

    def __init__(
        self,
        name: str,
        evaluator_type: EvaluatorType,
        threshold: float | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        if threshold is not None and not (0.0 <= threshold <= 1.0):
            raise ValueError(f"Threshold must be in range [0.0, 1.0], got {threshold}")
        self._name = name
        self._evaluator_type = evaluator_type
        self._threshold = threshold
        self._config = config or {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def evaluator_type(self) -> EvaluatorType:
        return self._evaluator_type

    @property
    def threshold(self) -> float | None:
        return self._threshold

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    @abstractmethod
    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> (
        tuple[float | None, str | None, float | None]
        | tuple[float | None, str | None, float | None, dict[str, Any] | None]
    ):
        """Internal scoring method to be implemented by subclasses.

        Returns:
            tuple of (score, explanation, confidence)
            or (score, explanation, confidence, metadata)
            - score: float in [0.0, 1.0] or None if unavailable
            - explanation: optional explanation string
            - confidence: optional confidence float in [0.0, 1.0]
            - metadata: optional evaluator-specific diagnostic metadata dictionary
        """
        ...

    async def evaluate(self, input_data: EvaluationInput) -> EvaluationMetric:
        """Evaluates input data with automated timing and error containment."""
        start_time = time.perf_counter()
        try:
            res = await self._compute_score(input_data)
        except Exception as err:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            if isinstance(err, EvaluationError):
                raise
            raise EvaluatorExecutionError(
                message=f"Evaluator '{self.name}' failed: {err}",
                evaluator_type=self.evaluator_type,
                case_id=input_data.case_id,
                details={"error": str(err)},
            ) from err

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        metric_metadata: dict[str, Any] = {}
        if len(res) == 4:
            score, explanation, confidence, extra_meta = res  # type: ignore[assignment]
            if isinstance(extra_meta, dict):
                metric_metadata = extra_meta
        else:
            score, explanation, confidence = res  # type: ignore[misc]

        if score is None:
            return EvaluationMetric(
                metric_name=self.name,
                evaluator_type=self.evaluator_type,
                score=None,
                passed=None,
                threshold=self.threshold,
                explanation=explanation,
                confidence=confidence,
                status=MetricStatus.UNAVAILABLE,
                execution_time_ms=elapsed_ms,
                metadata=metric_metadata,
            )

        if not (0.0 <= score <= 1.0):
            raise ValueError(
                f"Evaluator '{self.name}' returned score {score} outside [0.0, 1.0]"
            )

        passed: bool | None = None
        status = MetricStatus.SUCCESS
        if self.threshold is not None:
            passed = score >= self.threshold
            if not passed:
                status = MetricStatus.THRESHOLD_FAILED

        return EvaluationMetric(
            metric_name=self.name,
            evaluator_type=self.evaluator_type,
            score=score,
            passed=passed,
            threshold=self.threshold,
            explanation=explanation,
            confidence=confidence,
            status=status,
            execution_time_ms=elapsed_ms,
            metadata=metric_metadata,
        )
