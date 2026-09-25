from typing import Any

import pytest

from app.evaluation.adapters import (
    DeepEvalFaithfulnessEvaluator,
    DeepEvalHallucinationEvaluator,
    DeepEvalRelevanceEvaluator,
)
from app.evaluation.engine import EvaluationEngine
from app.evaluation.enums import EvaluatorBackend, EvaluatorType, MetricStatus
from app.evaluation.errors import (
    EvaluationTimeoutError,
    EvaluatorExecutionError,
    MalformedEvaluatorOutputError,
    UnsupportedEvaluatorError,
)
from app.evaluation.evaluators import ExactMatchEvaluator, RelevanceEvaluator
from app.evaluation.factory import create_evaluator
from app.evaluation.judges import BaseLLMJudge, JudgeRequest, JudgeResponse
from app.evaluation.registry import EvaluatorRegistry
from app.evaluation.types import EvaluationInput


class MockDeepEvalMetric:
    """Mock DeepEval metric object simulating a BaseMetric."""

    def __init__(
        self,
        score: float | None = 0.85,
        reason: str | None = "Response is highly relevant.",
        error: str | None = None,
        skipped: bool = False,
        is_async: bool = False,
    ) -> None:
        self.score = score
        self.reason = reason
        self.error = error
        self.skipped = skipped
        self.threshold = 0.7
        self.evaluation_model = "mock-gpt-4o"
        self.score_breakdown = {"relevance_statements": 3}
        self.evaluation_cost = 0.002
        self._is_async = is_async
        self.measured_test_cases: list[Any] = []

    def measure(self, test_case: Any) -> None:
        self.measured_test_cases.append(test_case)

    async def a_measure(self, test_case: Any) -> None:
        self.measured_test_cases.append(test_case)


class DummyMockJudge(BaseLLMJudge):
    @property
    def model_name(self) -> str:
        return "mock-judge"

    async def judge(self, request: JudgeRequest) -> JudgeResponse:
        return JudgeResponse(
            score=0.9,
            passed=True,
            rationale="Approved",
            metadata={"model": self.model_name},
        )


# ===========================================================================
# 1. Test Case Construction Tests
# ===========================================================================


def test_build_test_case_all_fields() -> None:
    evaluator = DeepEvalRelevanceEvaluator(name="test_rel", metric=MockDeepEvalMetric())

    input_data = EvaluationInput.create(
        input="Explain cellular respiration",
        response="Cellular respiration converts glucose into ATP...",
        expected_output="Biological process producing energy.",
        context=["Passage 1: Mitochondria produces ATP."],
    )

    test_case = evaluator._build_test_case(input_data)

    assert test_case.input == "Explain cellular respiration"
    assert test_case.actual_output is not None
    assert test_case.actual_output.startswith("Cellular respiration")
    assert test_case.expected_output == "Biological process producing energy."
    assert test_case.retrieval_context == ["Passage 1: Mitochondria produces ATP."]
    assert test_case.context == ["Passage 1: Mitochondria produces ATP."]


def test_build_test_case_dict_context() -> None:
    evaluator = DeepEvalRelevanceEvaluator(name="test_rel", metric=MockDeepEvalMetric())

    input_data = EvaluationInput.create(
        input="Query",
        response="Answer",
        context={"doc": "text_content", "id": 42},
    )

    test_case = evaluator._build_test_case(input_data)
    assert test_case.retrieval_context is not None
    assert len(test_case.retrieval_context) == 1
    assert '"doc": "text_content"' in test_case.retrieval_context[0]


def test_build_test_case_expected_output_as_fallback_context() -> None:
    evaluator = DeepEvalHallucinationEvaluator(
        name="test_hallucination", metric=MockDeepEvalMetric()
    )

    input_data = EvaluationInput.create(
        input="Where is the Taj Mahal?",
        response="Agra, India",
        expected_output="The Taj Mahal is in Agra, India.",
    )

    test_case = evaluator._build_test_case(input_data)
    assert test_case.retrieval_context is None
    assert test_case.context == ["The Taj Mahal is in Agra, India."]


# ===========================================================================
# 2. Metric Execution Model Tests (Async & Threaded Sync)
# ===========================================================================


@pytest.mark.asyncio
async def test_execute_deepeval_metric_async_coroutine() -> None:
    metric = MockDeepEvalMetric(is_async=True)
    evaluator = DeepEvalRelevanceEvaluator(name="test_rel", metric=metric)

    input_data = EvaluationInput.create(input="Q", response="A")
    test_case = evaluator._build_test_case(input_data)

    await evaluator._execute_deepeval_metric(metric, test_case)
    assert len(metric.measured_test_cases) == 1


@pytest.mark.asyncio
async def test_execute_deepeval_metric_sync_threaded() -> None:
    # Metric with only synchronous measure method
    class SyncOnlyMetric:
        def __init__(self) -> None:
            self.measured: list[Any] = []

        def measure(self, tc: Any) -> None:
            self.measured.append(tc)

    metric = SyncOnlyMetric()
    evaluator = DeepEvalRelevanceEvaluator(
        name="test_rel",
        metric=metric,  # type: ignore[arg-type]
    )

    input_data = EvaluationInput.create(input="Q", response="A")
    test_case = evaluator._build_test_case(input_data)

    await evaluator._execute_deepeval_metric(metric, test_case)
    assert len(metric.measured) == 1


@pytest.mark.asyncio
async def test_execute_deepeval_metric_unsupported_interface() -> None:
    class InvalidMetric:
        pass

    evaluator = DeepEvalRelevanceEvaluator(
        name="test_rel",
        metric=InvalidMetric(),  # type: ignore[arg-type]
    )

    input_data = EvaluationInput.create(input="Q", response="A")
    test_case = evaluator._build_test_case(input_data)

    with pytest.raises(EvaluatorExecutionError, match="neither measure nor a_measure"):
        await evaluator._execute_deepeval_metric(InvalidMetric(), test_case)


@pytest.mark.asyncio
async def test_execute_deepeval_metric_timeout() -> None:
    class SlowMetric:
        async def a_measure(self, tc: Any) -> None:
            raise TimeoutError("Metric timeout")

    evaluator = DeepEvalRelevanceEvaluator(
        name="test_rel",
        metric=SlowMetric(),  # type: ignore[arg-type]
    )

    input_data = EvaluationInput.create(input="Q", response="A")
    test_case = evaluator._build_test_case(input_data)

    with pytest.raises(EvaluationTimeoutError, match="timed out during execution"):
        await evaluator._execute_deepeval_metric(SlowMetric(), test_case)


@pytest.mark.asyncio
async def test_execute_deepeval_metric_credential_sanitization_on_error() -> None:
    class CrashingMetric:
        def measure(self, tc: Any) -> None:
            raise RuntimeError(
                "Upstream provider failure with key sk-secret-token-1234567890 "
                "and Bearer auth-token-xyz"
            )

    evaluator = DeepEvalRelevanceEvaluator(
        name="test_rel",
        metric=CrashingMetric(),  # type: ignore[arg-type]
    )

    input_data = EvaluationInput.create(input="Q", response="A")
    test_case = evaluator._build_test_case(input_data)

    with pytest.raises(EvaluatorExecutionError) as exc_info:
        await evaluator._execute_deepeval_metric(CrashingMetric(), test_case)

    err_str = str(exc_info.value)
    err_dict = exc_info.value.to_dict()
    assert "sk-secret-token-1234567890" not in err_str
    assert "auth-token-xyz" not in err_str
    assert "[REDACTED]" in err_dict["details"]["error"]


# ===========================================================================
# 3. Result Translation Tests
# ===========================================================================


def test_translate_result_success() -> None:
    metric = MockDeepEvalMetric(score=0.92, reason="Directly answers query")
    evaluator = DeepEvalRelevanceEvaluator(name="test_rel", metric=metric)

    score, reason, conf, meta = evaluator._translate_result(metric)
    assert score == 0.92
    assert reason == "Directly answers query"
    assert conf is None
    assert meta["backend"] == "deepeval"
    assert meta["deepeval_metric"] == "MockDeepEvalMetric"
    assert meta["evaluation_model"] == "mock-gpt-4o"
    assert meta["score_breakdown"] == {"relevance_statements": 3}


def test_translate_result_missing_score() -> None:
    metric = MockDeepEvalMetric(score=None, reason="Unable to score")
    evaluator = DeepEvalRelevanceEvaluator(name="test_rel", metric=metric)

    score, reason, conf, meta = evaluator._translate_result(metric)
    assert score is None
    assert reason == "Unable to score"


def test_translate_result_out_of_bounds_score_raises() -> None:
    metric_high = MockDeepEvalMetric(score=1.5)
    evaluator = DeepEvalRelevanceEvaluator(name="test_rel", metric=metric_high)
    with pytest.raises(MalformedEvaluatorOutputError, match="outside \\[0.0, 1.0\\]"):
        evaluator._translate_result(metric_high)

    metric_low = MockDeepEvalMetric(score=-0.2)
    with pytest.raises(MalformedEvaluatorOutputError, match="outside \\[0.0, 1.0\\]"):
        evaluator._translate_result(metric_low)


def test_translate_result_non_numeric_score_raises() -> None:
    metric = MockDeepEvalMetric(score="good")  # type: ignore[arg-type]
    evaluator = DeepEvalRelevanceEvaluator(name="test_rel", metric=metric)
    with pytest.raises(MalformedEvaluatorOutputError, match="non-numeric score"):
        evaluator._translate_result(metric)


def test_translate_result_skipped_metric() -> None:
    metric = MockDeepEvalMetric(
        score=None, reason="Skipped due to condition", skipped=True
    )
    evaluator = DeepEvalRelevanceEvaluator(name="test_rel", metric=metric)

    score, reason, conf, meta = evaluator._translate_result(metric)
    assert score is None
    assert reason == "Skipped due to condition"
    assert meta.get("skipped") is True


def test_translate_result_missing_input_error_returns_unavailable() -> None:
    metric = MockDeepEvalMetric(
        score=None, error="Retrieval context is required but missing"
    )
    evaluator = DeepEvalRelevanceEvaluator(name="test_rel", metric=metric)

    score, reason, conf, meta = evaluator._translate_result(metric)
    assert score is None
    assert "unavailable" in (reason or "").lower()


def test_translate_result_internal_error_raises_execution_error() -> None:
    metric = MockDeepEvalMetric(score=None, error="Fatal internal connection failure")
    evaluator = DeepEvalRelevanceEvaluator(name="test_rel", metric=metric)

    with pytest.raises(EvaluatorExecutionError, match="encountered error"):
        evaluator._translate_result(metric)


# ===========================================================================
# 4. DeepEvalRelevanceEvaluator Full Lifecycle
# ===========================================================================


@pytest.mark.asyncio
async def test_deepeval_relevance_evaluator_passed() -> None:
    metric = MockDeepEvalMetric(score=0.88, reason="Accurate relevance")
    evaluator = DeepEvalRelevanceEvaluator(threshold=0.7, metric=metric)

    input_data = EvaluationInput.create(
        input="How do rockets work?",
        response="Rockets produce thrust via Newton's third law.",
    )

    result = await evaluator.evaluate(input_data)
    assert result.evaluator_type == EvaluatorType.RELEVANCE
    assert result.score == 0.88
    assert result.passed is True
    assert result.status == MetricStatus.SUCCESS
    assert result.explanation == "Accurate relevance"
    assert result.metadata["backend"] == EvaluatorBackend.DEEPEVAL.value


@pytest.mark.asyncio
async def test_deepeval_relevance_evaluator_threshold_failed() -> None:
    metric = MockDeepEvalMetric(score=0.5, reason="Low relevance to query")
    evaluator = DeepEvalRelevanceEvaluator(threshold=0.7, metric=metric)

    input_data = EvaluationInput.create(
        input="How do rockets work?",
        response="Cars run on gasoline or electricity.",
    )

    result = await evaluator.evaluate(input_data)
    assert result.score == 0.5
    assert result.passed is False
    assert result.status == MetricStatus.THRESHOLD_FAILED


# ===========================================================================
# 5. DeepEvalFaithfulnessEvaluator Full Lifecycle
# ===========================================================================


@pytest.mark.asyncio
async def test_deepeval_faithfulness_missing_context_returns_unavailable() -> None:
    metric = MockDeepEvalMetric(score=1.0)
    evaluator = DeepEvalFaithfulnessEvaluator(threshold=0.7, metric=metric)

    # Missing context entirely
    input_data = EvaluationInput.create(
        input="Question",
        response="Answer",
    )

    result = await evaluator.evaluate(input_data)
    assert result.score is None
    assert result.passed is None
    assert result.status == MetricStatus.UNAVAILABLE
    assert "requires retrieved_context" in (result.explanation or "")
    assert len(metric.measured_test_cases) == 0


@pytest.mark.asyncio
async def test_deepeval_faithfulness_empty_context_list_returns_unavailable() -> None:
    metric = MockDeepEvalMetric(score=1.0)
    evaluator = DeepEvalFaithfulnessEvaluator(threshold=0.7, metric=metric)

    input_data = EvaluationInput.create(
        input="Question",
        response="Answer",
        context=[],
    )

    result = await evaluator.evaluate(input_data)
    assert result.score is None
    assert result.status == MetricStatus.UNAVAILABLE
    assert "non-empty" in (result.explanation or "")
    assert len(metric.measured_test_cases) == 0


@pytest.mark.asyncio
async def test_deepeval_faithfulness_success_with_context() -> None:
    metric = MockDeepEvalMetric(score=1.0, reason="100% faithful to retrieved passages")
    evaluator = DeepEvalFaithfulnessEvaluator(threshold=0.8, metric=metric)

    input_data = EvaluationInput.create(
        input="Policy duration?",
        response="The warranty policy lasts 24 months.",
        context=["Section 2.1: The product warranty covers 24 months."],
    )

    result = await evaluator.evaluate(input_data)
    assert result.evaluator_type == EvaluatorType.FAITHFULNESS
    assert result.score == 1.0
    assert result.passed is True
    assert result.status == MetricStatus.SUCCESS
    assert len(metric.measured_test_cases) == 1


# ===========================================================================
# 6. DeepEvalHallucinationEvaluator Full Lifecycle
# ===========================================================================


@pytest.mark.asyncio
async def test_deepeval_hallucination_missing_prerequisites() -> None:
    metric = MockDeepEvalMetric(score=1.0)
    evaluator = DeepEvalHallucinationEvaluator(threshold=0.7, metric=metric)

    # Neither context nor expected_output provided
    input_data = EvaluationInput.create(
        input="Question",
        response="Answer",
    )

    result = await evaluator.evaluate(input_data)
    assert result.score is None
    assert result.status == MetricStatus.UNAVAILABLE
    assert "requires context or expected_output" in (result.explanation or "")
    assert len(metric.measured_test_cases) == 0


@pytest.mark.asyncio
async def test_deepeval_hallucination_score_direction_no_hallucination() -> None:
    # EVALX standard: 1.0 = No hallucination / clean (higher is better)
    metric = MockDeepEvalMetric(
        score=1.0, reason="No hallucinations detected in response."
    )
    evaluator = DeepEvalHallucinationEvaluator(threshold=0.7, metric=metric)

    input_data = EvaluationInput.create(
        input="Where is the Colosseum?",
        response="The Colosseum is in Rome, Italy.",
        expected_output="Rome, Italy",
    )

    result = await evaluator.evaluate(input_data)
    assert result.evaluator_type == EvaluatorType.HALLUCINATION
    assert result.score == 1.0
    assert result.passed is True
    assert result.status == MetricStatus.SUCCESS


@pytest.mark.asyncio
async def test_deepeval_hallucination_score_direction_severe_hallucination() -> None:
    # 0.2 = Severe hallucination -> threshold failed
    metric = MockDeepEvalMetric(
        score=0.2, reason="Response contains multiple fabricated facts."
    )
    evaluator = DeepEvalHallucinationEvaluator(threshold=0.7, metric=metric)

    input_data = EvaluationInput.create(
        input="Where is the Colosseum?",
        response="The Colosseum is made of cheese on the moon.",
        expected_output="Rome, Italy",
    )

    result = await evaluator.evaluate(input_data)
    assert result.score == 0.2
    assert result.passed is False
    assert result.status == MetricStatus.THRESHOLD_FAILED


# ===========================================================================
# 7. Backend Selection & Factory Tests
# ===========================================================================


def test_factory_create_deepeval_evaluators() -> None:
    rel_eval = create_evaluator(
        EvaluatorType.RELEVANCE,
        backend=EvaluatorBackend.DEEPEVAL,
        metric=MockDeepEvalMetric(),
    )
    assert isinstance(rel_eval, DeepEvalRelevanceEvaluator)
    assert rel_eval.name == "deepeval_relevance"

    faith_eval = create_evaluator(
        "faithfulness",
        backend="deepeval",
        metric=MockDeepEvalMetric(),
    )
    assert isinstance(faith_eval, DeepEvalFaithfulnessEvaluator)
    assert faith_eval.name == "deepeval_faithfulness"

    halluc_eval = create_evaluator(
        EvaluatorType.HALLUCINATION,
        backend=EvaluatorBackend.DEEPEVAL,
        metric=MockDeepEvalMetric(),
    )
    assert isinstance(halluc_eval, DeepEvalHallucinationEvaluator)
    assert halluc_eval.name == "deepeval_hallucination"


def test_factory_create_llm_judge_evaluators() -> None:
    mock_judge = DummyMockJudge()
    fact_eval = create_evaluator(
        EvaluatorType.FACTUALITY,
        backend=EvaluatorBackend.LLM_JUDGE,
        judge=mock_judge,
    )
    assert fact_eval.name == "factuality"
    assert fact_eval.evaluator_type == EvaluatorType.FACTUALITY

    rel_eval = create_evaluator(
        EvaluatorType.RELEVANCE,
        backend=EvaluatorBackend.LLM_JUDGE,
        judge=mock_judge,
    )
    assert rel_eval.name == "relevance"
    assert rel_eval.evaluator_type == EvaluatorType.RELEVANCE


def test_factory_create_native_deterministic() -> None:
    exact_eval = create_evaluator(
        EvaluatorType.INSTRUCTION_FOLLOWING,
        backend=EvaluatorBackend.NATIVE,
    )
    assert isinstance(exact_eval, ExactMatchEvaluator)
    assert exact_eval.name == "exact_match"


def test_factory_unsupported_deepeval_metric_raises() -> None:
    with pytest.raises(UnsupportedEvaluatorError, match="does not support"):
        create_evaluator(
            EvaluatorType.FACTUALITY,
            backend=EvaluatorBackend.DEEPEVAL,
        )

    with pytest.raises(UnsupportedEvaluatorError, match="does not support"):
        create_evaluator(
            EvaluatorType.CONSISTENCY,
            backend=EvaluatorBackend.DEEPEVAL,
        )


def test_factory_invalid_backend_or_type_raises() -> None:
    with pytest.raises(
        UnsupportedEvaluatorError, match="Unsupported evaluator backend"
    ):
        create_evaluator(EvaluatorType.RELEVANCE, backend="nonexistent_backend")

    with pytest.raises(UnsupportedEvaluatorError, match="Unsupported evaluator type"):
        create_evaluator("totally_bogus_type", backend=EvaluatorBackend.DEEPEVAL)


# ===========================================================================
# 8. Registry and Engine Integration Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_engine_mixed_native_judge_and_deepeval_evaluators() -> None:
    registry = EvaluatorRegistry()

    # 1. Deterministic evaluator
    registry.register(ExactMatchEvaluator(name="exact_match"))

    # 2. Native LLM-Judge evaluator
    judge = DummyMockJudge()
    registry.register(RelevanceEvaluator(name="llm_relevance", judge=judge))

    # 3. DeepEval adapter evaluator
    deepeval_metric = MockDeepEvalMetric(
        score=0.95, reason="DeepEval high faithfulness"
    )
    registry.register(
        DeepEvalFaithfulnessEvaluator(
            name="deepeval_faithfulness",
            metric=deepeval_metric,
        )
    )

    assert len(registry) == 3
    assert "exact_match" in registry.list_evaluators()
    assert "llm_relevance" in registry.list_evaluators()
    assert "deepeval_faithfulness" in registry.list_evaluators()

    engine = EvaluationEngine(registry=registry)

    input_data = EvaluationInput.create(
        input="What is the boiling point of water?",
        response="100 degrees Celsius",
        expected_output="100 degrees Celsius",
        context=["Water boils at 100 degrees Celsius at sea level."],
    )

    case_result = await engine.evaluate_case(input_data)

    assert case_result.passed is True
    assert len(case_result.metrics) == 3
    assert case_result.metrics["exact_match"].score == 1.0
    assert case_result.metrics["llm_relevance"].score == 0.9
    assert case_result.metrics["deepeval_faithfulness"].score == 0.95
    assert len(case_result.errors) == 0


@pytest.mark.asyncio
async def test_engine_deepeval_evaluator_error_containment() -> None:
    class FailingMetric:
        def measure(self, tc: Any) -> None:
            raise RuntimeError("DeepEval API connection crash")

    registry = EvaluatorRegistry()
    registry.register(ExactMatchEvaluator(name="exact_match"))
    registry.register(
        DeepEvalRelevanceEvaluator(
            name="deepeval_failing",
            metric=FailingMetric(),  # type: ignore[arg-type]
        )
    )

    engine = EvaluationEngine(registry=registry)

    input_data = EvaluationInput.create(
        input="Target query",
        response="Target response",
        expected_output="Target response",
    )

    case_result = await engine.evaluate_case(input_data)

    # ExactMatch passes
    assert case_result.metrics["exact_match"].score == 1.0
    assert case_result.metrics["exact_match"].passed is True

    # DeepEval fails but error is contained in engine
    assert case_result.metrics["deepeval_failing"].status == MetricStatus.ERROR
    assert len(case_result.errors) == 1
    assert "DeepEval" in case_result.errors[0]
