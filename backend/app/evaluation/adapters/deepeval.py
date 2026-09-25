import asyncio
import inspect
import json
import re
from typing import Any

from deepeval.test_case import LLMTestCase  # type: ignore[import-untyped]

from app.evaluation.contracts import AbstractEvaluator
from app.evaluation.enums import EvaluatorBackend, EvaluatorType
from app.evaluation.errors import (
    EvaluationError,
    EvaluationTimeoutError,
    EvaluatorExecutionError,
    MalformedEvaluatorOutputError,
    sanitize_details,
)
from app.evaluation.types import EvaluationInput


def _sanitize_error_str(text: str) -> str:
    """Redact sensitive authorization tokens, keys, and secrets from text."""
    sanitized = re.sub(r"Bearer\s+[\w\-\.]+", "Bearer [REDACTED]", text)
    sanitized = re.sub(
        r"(?:sk-|key-|token-|secret-)[\w\-\.]{10,}", "[REDACTED]", sanitized
    )
    return sanitized


class DeepEvalBaseAdapterEvaluator(AbstractEvaluator):
    """Abstract base adapter connecting DeepEval metrics to EVALX contracts.

    Translates EVALX EvaluationInput into DeepEval LLMTestCase, executes the
    metric asynchronously (offloading synchronous calls to worker threads),
    and maps results back to EVALX EvaluationMetric semantics.
    """

    def __init__(
        self,
        name: str,
        evaluator_type: EvaluatorType,
        threshold: float | None = 0.7,
        metric: Any | None = None,
        model: str | Any | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=evaluator_type,
            threshold=threshold,
            config=config,
        )
        self._backend = EvaluatorBackend.DEEPEVAL
        self._metric = metric
        self._model = model

    @property
    def backend(self) -> EvaluatorBackend:
        return self._backend

    @property
    def metric(self) -> Any:
        if self._metric is None:
            self._metric = self._init_metric()
        return self._metric

    def _init_metric(self) -> Any:
        """Instantiate the underlying DeepEval metric. Implemented by subclasses."""
        raise NotImplementedError(
            f"Evaluator '{self.name}' must implement _init_metric()"
        )

    def _check_preconditions(self, input_data: EvaluationInput) -> str | None:
        """Validate prerequisites before executing DeepEval.

        Returns an unavailability reason string if inputs are missing,
        or None if all required prerequisites are satisfied.
        """
        return None

    def _build_test_case(self, input_data: EvaluationInput) -> LLMTestCase:
        """Convert EVALX EvaluationInput into DeepEval LLMTestCase."""
        retrieval_context: list[Any] | None = None
        if input_data.retrieved_context is not None:
            if isinstance(input_data.retrieved_context, list):
                retrieval_context = [str(p) for p in input_data.retrieved_context]
            elif isinstance(input_data.retrieved_context, dict):
                retrieval_context = [json.dumps(input_data.retrieved_context)]
            else:
                retrieval_context = [str(input_data.retrieved_context)]

        # HallucinationMetric and others may check `context`
        context: list[Any] | None = None
        if retrieval_context:
            context = retrieval_context
        elif input_data.expected_output:
            context = [input_data.expected_output]

        return LLMTestCase(
            input=input_data.input,
            actual_output=input_data.response,
            expected_output=input_data.expected_output,
            retrieval_context=retrieval_context,
            context=context,
        )

    async def _execute_deepeval_metric(
        self, metric: Any, test_case: LLMTestCase
    ) -> None:
        """Execute DeepEval metric preserving async loop responsiveness."""
        try:
            if hasattr(metric, "a_measure") and inspect.iscoroutinefunction(
                metric.a_measure
            ):
                await metric.a_measure(test_case)
            elif hasattr(metric, "measure"):
                # Run synchronous measure in thread pool to prevent blocking event loop
                await asyncio.to_thread(metric.measure, test_case)
            else:
                raise EvaluatorExecutionError(
                    f"DeepEval metric '{type(metric).__name__}' provides neither "
                    f"measure nor a_measure.",
                    evaluator_type=self.evaluator_type,
                )
        except TimeoutError as err:
            raise EvaluationTimeoutError(
                f"DeepEval metric '{self.name}' timed out during execution: {err}",
                evaluator_type=self.evaluator_type,
                details={"metric": type(metric).__name__},
            ) from err
        except EvaluationError:
            raise
        except Exception as err:
            safe_msg = _sanitize_error_str(str(err))
            raise EvaluatorExecutionError(
                f"DeepEval metric '{self.name}' execution failed: {type(err).__name__}",
                evaluator_type=self.evaluator_type,
                details=sanitize_details(
                    {"metric": type(metric).__name__, "error": safe_msg}
                ),
            ) from err

    def _translate_result(
        self, metric: Any
    ) -> tuple[float | None, str | None, float | None, dict[str, Any]]:
        """Translate DeepEval metric result attributes to EVALX scoring model."""
        err_attr = getattr(metric, "error", None)
        if err_attr:
            safe_err = _sanitize_error_str(str(err_attr))
            lower_err = safe_err.lower()
            if any(
                term in lower_err
                for term in ("context", "missing", "required input", "retrieval")
            ):
                return (
                    None,
                    f"DeepEval evaluation unavailable: {safe_err}",
                    None,
                    {
                        "backend": EvaluatorBackend.DEEPEVAL.value,
                        "deepeval_error": safe_err,
                    },
                )
            raise EvaluatorExecutionError(
                f"DeepEval metric '{self.name}' encountered error: {safe_err}",
                evaluator_type=self.evaluator_type,
                details=sanitize_details(
                    {"metric": type(metric).__name__, "error": safe_err}
                ),
            )

        if getattr(metric, "skipped", False):
            reason = (
                getattr(metric, "reason", None) or "Metric was skipped by DeepEval."
            )
            return (
                None,
                reason,
                None,
                {"backend": EvaluatorBackend.DEEPEVAL.value, "skipped": True},
            )

        raw_score = getattr(metric, "score", None)
        if raw_score is None:
            reason = (
                getattr(metric, "reason", None) or "DeepEval metric returned no score."
            )
            return (
                None,
                reason,
                None,
                {"backend": EvaluatorBackend.DEEPEVAL.value},
            )

        try:
            score = float(raw_score)
        except (TypeError, ValueError) as exc:
            raise MalformedEvaluatorOutputError(
                f"DeepEval metric '{self.name}' returned non-numeric "
                f"score: {raw_score}",
                evaluator_type=self.evaluator_type,
                details={"raw_score": str(raw_score)},
            ) from exc

        if not (0.0 <= score <= 1.0):
            raise MalformedEvaluatorOutputError(
                f"DeepEval metric '{self.name}' returned score {score} "
                "outside [0.0, 1.0]",
                evaluator_type=self.evaluator_type,
                details={"score": score},
            )

        reason = getattr(metric, "reason", None)
        metadata: dict[str, Any] = {
            "backend": EvaluatorBackend.DEEPEVAL.value,
            "deepeval_metric": type(metric).__name__,
            "evaluation_model": getattr(metric, "evaluation_model", None),
            "score_breakdown": getattr(metric, "score_breakdown", None),
            "evaluation_cost": getattr(metric, "evaluation_cost", None),
        }
        return (score, reason, None, metadata)

    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> (
        tuple[float | None, str | None, float | None]
        | tuple[float | None, str | None, float | None, dict[str, Any] | None]
    ):
        unavailability_reason = self._check_preconditions(input_data)
        if unavailability_reason is not None:
            return (
                None,
                unavailability_reason,
                None,
                {"backend": EvaluatorBackend.DEEPEVAL.value},
            )

        test_case = self._build_test_case(input_data)
        metric = self.metric
        await self._execute_deepeval_metric(metric, test_case)
        return self._translate_result(metric)


class DeepEvalRelevanceEvaluator(DeepEvalBaseAdapterEvaluator):
    """Evaluates query relevance using DeepEval's AnswerRelevancyMetric.

    Measures whether the candidate response directly, specifically, and
    relevantly answers the input query without extraneous statements.
    """

    def __init__(
        self,
        name: str = "deepeval_relevance",
        threshold: float | None = 0.7,
        metric: Any | None = None,
        model: str | Any | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=EvaluatorType.RELEVANCE,
            threshold=threshold,
            metric=metric,
            model=model,
            config=config,
        )

    def _init_metric(self) -> Any:
        from deepeval.metrics import (  # type: ignore[import-untyped]
            AnswerRelevancyMetric,
        )

        return AnswerRelevancyMetric(
            threshold=self.threshold if self.threshold is not None else 0.7,
            model=self._model,
            include_reason=True,
            async_mode=True,
        )


class DeepEvalFaithfulnessEvaluator(DeepEvalBaseAdapterEvaluator):
    """Evaluates RAG context faithfulness using DeepEval's FaithfulnessMetric.

    Measures whether every claim in the response is inferable from retrieved context.
    Returns MetricStatus.UNAVAILABLE if retrieved context passages are missing or empty.
    """

    def __init__(
        self,
        name: str = "deepeval_faithfulness",
        threshold: float | None = 0.7,
        metric: Any | None = None,
        model: str | Any | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=EvaluatorType.FAITHFULNESS,
            threshold=threshold,
            metric=metric,
            model=model,
            config=config,
        )

    def _check_preconditions(self, input_data: EvaluationInput) -> str | None:
        if input_data.retrieved_context is None:
            return (
                "DeepEvalFaithfulnessEvaluator requires retrieved_context "
                "passages, but none were provided."
            )
        if (
            isinstance(input_data.retrieved_context, list)
            and len(input_data.retrieved_context) == 0
        ) or not input_data.retrieved_context:
            return (
                "DeepEvalFaithfulnessEvaluator requires non-empty "
                "retrieved_context passages."
            )
        return None

    def _init_metric(self) -> Any:
        from deepeval.metrics import (  # type: ignore[import-untyped]
            FaithfulnessMetric,
        )

        return FaithfulnessMetric(
            threshold=self.threshold if self.threshold is not None else 0.7,
            model=self._model,
            include_reason=True,
            async_mode=True,
        )


class DeepEvalHallucinationEvaluator(DeepEvalBaseAdapterEvaluator):
    """Evaluates contextual hallucination using DeepEval's HallucinationMetric.

    Measures the ratio of factually aligned statements against provided context
    or expected ground truth.
    Returns MetricStatus.UNAVAILABLE if neither context nor expected_output is provided.
    Score 1.0 indicates zero hallucinations (clean/truthful);
    Score 0.0 indicates severe hallucinations.
    """

    def __init__(
        self,
        name: str = "deepeval_hallucination",
        threshold: float | None = 0.7,
        metric: Any | None = None,
        model: str | Any | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=EvaluatorType.HALLUCINATION,
            threshold=threshold,
            metric=metric,
            model=model,
            config=config,
        )

    def _check_preconditions(self, input_data: EvaluationInput) -> str | None:
        has_expected = bool(
            input_data.expected_output and input_data.expected_output.strip()
        )
        has_context = bool(input_data.retrieved_context)
        if not has_expected and not has_context:
            return (
                "DeepEvalHallucinationEvaluator requires context or "
                "expected_output ground truth, but neither was provided."
            )
        return None

    def _init_metric(self) -> Any:
        from deepeval.metrics import (  # type: ignore[import-untyped]
            HallucinationMetric,
        )

        return HallucinationMetric(
            threshold=self.threshold if self.threshold is not None else 0.7,
            model=self._model,
            include_reason=True,
            async_mode=True,
        )
