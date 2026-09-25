import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.evaluation.contracts import AbstractEvaluator, BaseEvaluator
from app.evaluation.engine import EvaluationEngine
from app.evaluation.enums import EvaluatorType, MetricStatus, ScoreDirection
from app.evaluation.errors import (
    EvaluationError,
    EvaluationTimeoutError,
    EvaluatorExecutionError,
    InvalidEvaluationInputError,
    MalformedEvaluatorOutputError,
    ModelResponseUnavailableError,
    UnavailableDependencyError,
    UnsupportedEvaluatorError,
    sanitize_details,
    sanitize_error_message,
    sanitize_url_credentials,
)
from app.evaluation.scoring import (
    calculate_case_score,
    calculate_run_score,
    evaluate_metric_threshold,
    validate_score_range,
)
from app.evaluation.types import (
    EvaluationCaseResult,
    EvaluationContext,
    EvaluationInput,
    EvaluationMetric,
    ModelInfo,
)


class DeterministicMatchEvaluator(AbstractEvaluator):
    """Deterministic mock evaluator implementing BaseEvaluator for testing."""

    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> tuple[float | None, str | None, float | None]:
        if not input_data.expected_output:
            return None, "No expected output provided", None

        is_match = (
            input_data.response.strip().lower()
            == input_data.expected_output.strip().lower()
        )
        score = 1.0 if is_match else 0.0
        explanation = "Exact match" if is_match else "Content mismatch"
        return score, explanation, 1.0


class FailingEvaluator(AbstractEvaluator):
    """Evaluator that simulates an unexpected runtime failure."""

    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> tuple[float | None, str | None, float | None]:
        raise RuntimeError("External service connection failed")


# ---------------------------------------------------------------------------
# 1. Valid EvaluationInput
# ---------------------------------------------------------------------------


def test_valid_evaluation_input_creation() -> None:
    case_id = uuid.uuid4()
    model_info = ModelInfo(
        provider="openai",
        model_name="gpt-4o",
        parameters={"temperature": 0.2, "max_tokens": 500},
    )
    eval_input = EvaluationInput.create(
        case_id=case_id,
        input="What is the capital of France?",
        response="Paris is the capital of France.",
        expected_output="Paris",
        context=["France is a country in Europe. Its capital is Paris."],
        metadata={"category": "geography", "difficulty": "easy"},
        model_info=model_info,
        system_prompt="You are a helpful geography assistant.",
    )

    assert eval_input.case_id == case_id
    assert eval_input.response == "Paris is the capital of France."
    assert eval_input.input == "What is the capital of France?"
    assert eval_input.expected_output == "Paris"
    assert eval_input.retrieved_context == [
        "France is a country in Europe. Its capital is Paris."
    ]
    assert eval_input.metadata == {"category": "geography", "difficulty": "easy"}
    assert eval_input.model_info == model_info
    assert eval_input.system_prompt == "You are a helpful geography assistant."


def test_evaluation_input_from_nested_context() -> None:
    ctx = EvaluationContext(
        input="Explain photosynthesis.",
        expected_output="Photosynthesis converts sunlight into chemical energy.",
    )
    eval_input = EvaluationInput(
        response="Plants use sunlight to make food.",
        context=ctx,
    )
    assert eval_input.input == "Explain photosynthesis."
    assert eval_input.response == "Plants use sunlight to make food."
    assert eval_input.expected_output == (
        "Photosynthesis converts sunlight into chemical energy."
    )


# ---------------------------------------------------------------------------
# 2. Invalid EvaluationInput
# ---------------------------------------------------------------------------


def test_invalid_evaluation_input_empty_or_blank() -> None:
    # Empty response
    with pytest.raises(ValidationError):
        EvaluationInput.create(input="Valid prompt", response="")

    # Whitespace-only response
    with pytest.raises(ValidationError):
        EvaluationInput.create(input="Valid prompt", response="   \n\t  ")

    # Empty prompt/input
    with pytest.raises(ValidationError):
        EvaluationInput.create(input="", response="Valid response")

    # Whitespace-only prompt/input
    with pytest.raises(ValidationError):
        EvaluationInput.create(input="   ", response="Valid response")


# ---------------------------------------------------------------------------
# 3. Evaluator Contract Behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluator_protocol_conformance() -> None:
    evaluator = DeterministicMatchEvaluator(
        name="exact_match",
        evaluator_type=EvaluatorType.FACTUALITY,
        threshold=0.8,
    )

    assert isinstance(evaluator, BaseEvaluator)
    assert evaluator.name == "exact_match"
    assert evaluator.evaluator_type == EvaluatorType.FACTUALITY
    assert evaluator.threshold == 0.8

    input_data = EvaluationInput.create(
        input="2 + 2",
        response="4",
        expected_output="4",
    )
    metric = await evaluator.evaluate(input_data)

    assert isinstance(metric, EvaluationMetric)
    assert metric.metric_name == "exact_match"
    assert metric.evaluator_type == EvaluatorType.FACTUALITY
    assert metric.score == 1.0
    assert metric.passed is True
    assert metric.status == MetricStatus.SUCCESS
    assert metric.explanation == "Exact match"
    assert metric.confidence == 1.0
    assert metric.execution_time_ms is not None
    assert metric.execution_time_ms >= 0.0


# ---------------------------------------------------------------------------
# 4. Metric Validation
# ---------------------------------------------------------------------------


def test_metric_validation_fields() -> None:
    metric = EvaluationMetric(
        metric_name="factuality_eval",
        evaluator_type=EvaluatorType.FACTUALITY,
        score=0.92,
        passed=True,
        threshold=0.85,
        explanation="High factual accuracy verified against reference.",
        confidence=0.95,
        status=MetricStatus.SUCCESS,
        execution_time_ms=12.4,
        metadata={"token_count": 45},
    )

    assert metric.metric_name == "factuality_eval"
    assert metric.evaluator_type == EvaluatorType.FACTUALITY
    assert metric.score == 0.92
    assert metric.passed is True
    assert metric.threshold == 0.85
    assert metric.confidence == 0.95
    assert metric.status == MetricStatus.SUCCESS
    assert metric.metadata["token_count"] == 45


# ---------------------------------------------------------------------------
# 5. Score Bounds
# ---------------------------------------------------------------------------


def test_score_bounds_enforcement() -> None:
    # Score out of bounds
    with pytest.raises(ValidationError):
        EvaluationMetric(
            metric_name="m",
            evaluator_type=EvaluatorType.RELEVANCE,
            score=-0.01,
        )

    with pytest.raises(ValidationError):
        EvaluationMetric(
            metric_name="m",
            evaluator_type=EvaluatorType.RELEVANCE,
            score=1.01,
        )

    # Threshold out of bounds
    with pytest.raises(ValidationError):
        EvaluationMetric(
            metric_name="m",
            evaluator_type=EvaluatorType.RELEVANCE,
            score=0.5,
            threshold=1.5,
        )

    # Evaluator constructor rejects invalid threshold
    with pytest.raises(ValueError, match="Threshold must be in range"):
        DeterministicMatchEvaluator(
            name="m",
            evaluator_type=EvaluatorType.FACTUALITY,
            threshold=-0.1,
        )

    # validate_score_range helper
    validate_score_range(0.0)
    validate_score_range(1.0)
    validate_score_range(0.5)
    with pytest.raises(ValueError, match="must be within range"):
        validate_score_range(1.05)


# ---------------------------------------------------------------------------
# 6. Threshold Behavior
# ---------------------------------------------------------------------------


def test_threshold_evaluation_rules() -> None:
    # Score meets threshold -> passed=True, status=SUCCESS
    passed, status = evaluate_metric_threshold(score=0.85, threshold=0.80)
    assert passed is True
    assert status == MetricStatus.SUCCESS

    # Score exactly equals threshold -> passed=True
    passed, status = evaluate_metric_threshold(score=0.80, threshold=0.80)
    assert passed is True
    assert status == MetricStatus.SUCCESS

    # Score below threshold -> passed=False, status=THRESHOLD_FAILED
    passed, status = evaluate_metric_threshold(score=0.79, threshold=0.80)
    assert passed is False
    assert status == MetricStatus.THRESHOLD_FAILED

    # No threshold configured -> passed=None, status=SUCCESS
    passed, status = evaluate_metric_threshold(score=0.85, threshold=None)
    assert passed is None
    assert status == MetricStatus.SUCCESS

    # Score is None -> passed=None, status=UNAVAILABLE
    passed, status = evaluate_metric_threshold(score=None, threshold=0.80)
    assert passed is None
    assert status == MetricStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# 7. Aggregation Behavior
# ---------------------------------------------------------------------------


def test_case_scoring_unweighted() -> None:
    metrics = {
        "factuality": EvaluationMetric(
            metric_name="factuality",
            evaluator_type=EvaluatorType.FACTUALITY,
            score=0.9,
            passed=True,
            threshold=0.8,
            status=MetricStatus.SUCCESS,
        ),
        "relevance": EvaluationMetric(
            metric_name="relevance",
            evaluator_type=EvaluatorType.RELEVANCE,
            score=0.8,
            passed=True,
            threshold=0.7,
            status=MetricStatus.SUCCESS,
        ),
        "faithfulness": EvaluationMetric(
            metric_name="faithfulness",
            evaluator_type=EvaluatorType.FAITHFULNESS,
            score=1.0,
            passed=True,
            threshold=0.9,
            status=MetricStatus.SUCCESS,
        ),
    }

    # Mean: (0.9 + 0.8 + 1.0) / 3 = 0.9
    overall_score, passed = calculate_case_score(metrics)
    assert overall_score == 0.9
    assert passed is True


def test_case_scoring_weighted() -> None:
    metrics = {
        "factuality": EvaluationMetric(
            metric_name="factuality",
            evaluator_type=EvaluatorType.FACTUALITY,
            score=0.9,
            status=MetricStatus.SUCCESS,
        ),
        "relevance": EvaluationMetric(
            metric_name="relevance",
            evaluator_type=EvaluatorType.RELEVANCE,
            score=0.6,
            status=MetricStatus.SUCCESS,
        ),
    }

    # Weights: factuality=3.0, relevance=1.0 -> (0.9*3 + 0.6*1) / 4 = 3.3 / 4 = 0.825
    weights = {"factuality": 3.0, "relevance": 1.0}
    overall_score, _ = calculate_case_score(metrics, weights=weights)
    assert overall_score == 0.825


def test_case_scoring_failure_propagation() -> None:
    metrics = {
        "factuality": EvaluationMetric(
            metric_name="factuality",
            evaluator_type=EvaluatorType.FACTUALITY,
            score=0.9,
            passed=True,
            threshold=0.8,
            status=MetricStatus.SUCCESS,
        ),
        "hallucination": EvaluationMetric(
            metric_name="hallucination",
            evaluator_type=EvaluatorType.HALLUCINATION,
            score=0.4,
            passed=False,
            threshold=0.7,
            status=MetricStatus.THRESHOLD_FAILED,
        ),
    }
    overall_score, passed = calculate_case_score(metrics)
    assert overall_score == 0.65
    assert passed is False  # Explicit failure flags case as failed


def test_run_score_aggregation() -> None:
    cases = [
        EvaluationCaseResult(
            overall_score=0.80,
            passed=True,
            metrics={
                "factuality": EvaluationMetric(
                    metric_name="factuality",
                    evaluator_type=EvaluatorType.FACTUALITY,
                    score=0.80,
                )
            },
        ),
        EvaluationCaseResult(
            overall_score=0.90,
            passed=True,
            metrics={
                "factuality": EvaluationMetric(
                    metric_name="factuality",
                    evaluator_type=EvaluatorType.FACTUALITY,
                    score=0.90,
                )
            },
        ),
        EvaluationCaseResult(
            overall_score=1.00,
            passed=True,
            metrics={
                "factuality": EvaluationMetric(
                    metric_name="factuality",
                    evaluator_type=EvaluatorType.FACTUALITY,
                    score=1.00,
                )
            },
        ),
    ]

    overall_run, metric_avgs, completed, failed = calculate_run_score(cases)
    assert overall_run == 0.90
    assert metric_avgs == {"factuality": 0.90}
    assert completed == 3
    assert failed == 0


# ---------------------------------------------------------------------------
# 8. Missing Metric Handling
# ---------------------------------------------------------------------------


def test_missing_and_unavailable_metric_handling() -> None:
    metrics = {
        "factuality": EvaluationMetric(
            metric_name="factuality",
            evaluator_type=EvaluatorType.FACTUALITY,
            score=0.8,
            status=MetricStatus.SUCCESS,
        ),
        "faithfulness": EvaluationMetric(
            metric_name="faithfulness",
            evaluator_type=EvaluatorType.FAITHFULNESS,
            score=None,
            status=MetricStatus.UNAVAILABLE,
            explanation="Context was not available for faithfulness check",
        ),
    }

    # Unavailable metric is excluded from both numerator and denominator
    overall_score, passed = calculate_case_score(metrics)
    assert overall_score == 0.8
    assert passed is None


def test_all_metrics_missing_returns_none() -> None:
    metrics = {
        "faithfulness": EvaluationMetric(
            metric_name="faithfulness",
            evaluator_type=EvaluatorType.FAITHFULNESS,
            score=None,
            status=MetricStatus.UNAVAILABLE,
        ),
    }
    overall_score, passed = calculate_case_score(metrics)
    assert overall_score is None
    assert passed is None


def test_error_status_metric_handling() -> None:
    metrics = {
        "factuality": EvaluationMetric(
            metric_name="factuality",
            evaluator_type=EvaluatorType.FACTUALITY,
            score=0.9,
            status=MetricStatus.SUCCESS,
        ),
        "relevance": EvaluationMetric(
            metric_name="relevance",
            evaluator_type=EvaluatorType.RELEVANCE,
            score=None,
            status=MetricStatus.ERROR,
            explanation="Evaluator crashed",
        ),
    }
    overall_score, passed = calculate_case_score(metrics)
    assert overall_score == 0.9
    assert passed is False  # Error marks the case as passed=False


# ---------------------------------------------------------------------------
# 9. Structured Evaluation Errors & Sanitization
# ---------------------------------------------------------------------------


def test_structured_evaluation_errors_and_sanitization() -> None:
    raw_details: dict[str, Any] = {
        "api_key": "sk-secret-12345",
        "authorization": "Bearer eyJhbGciOi...",
        "token": "tok_abc987",
        "nested": {
            "password": "super-secret-password",
            "model_provider": "anthropic",
        },
        "query_tokens": 128,
    }

    sanitized = sanitize_details(raw_details)
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["authorization"] == "[REDACTED]"
    assert sanitized["token"] == "[REDACTED]"
    assert sanitized["nested"]["password"] == "[REDACTED]"
    assert sanitized["nested"]["model_provider"] == "anthropic"
    assert sanitized["query_tokens"] == 128

    err = EvaluatorExecutionError(
        message="Model API rejected request",
        evaluator_type=EvaluatorType.INSTRUCTION_FOLLOWING,
        details=raw_details,
    )
    err_dict = err.to_dict()
    assert err_dict["error_type"] == "EvaluatorExecutionError"
    assert err_dict["evaluator_type"] == "instruction_following"
    assert err_dict["details"]["api_key"] == "[REDACTED]"
    assert "api_key" not in str(err)  # String representation is safe


def test_all_evaluation_error_subclasses() -> None:
    assert issubclass(InvalidEvaluationInputError, EvaluationError)
    assert issubclass(UnsupportedEvaluatorError, EvaluationError)
    assert issubclass(EvaluatorExecutionError, EvaluationError)
    assert issubclass(ModelResponseUnavailableError, EvaluationError)
    assert issubclass(MalformedEvaluatorOutputError, EvaluationError)
    assert issubclass(EvaluationTimeoutError, EvaluationError)
    assert issubclass(UnavailableDependencyError, EvaluationError)


def test_sanitize_url_credentials_and_error_message() -> None:
    # 1. URL credential masking
    redis_url = "redis://evalx:supersecret@my-redis-host:6379/0"
    sanitized_url = sanitize_url_credentials(redis_url)
    assert "supersecret" not in sanitized_url
    assert "evalx" not in sanitized_url
    assert sanitized_url == "redis://[REDACTED]@my-redis-host:6379/0"

    pg_url = "postgresql://user:pass@localhost:5432/evalx"
    assert (
        sanitize_url_credentials(pg_url)
        == "postgresql://[REDACTED]@localhost:5432/evalx"
    )

    # 2. General error message sanitization
    raw_msg = (
        "Connection refused at redis://user:secretpass@10.0.0.1:6379/0 "
        "with password=secret_value and api_key=xyz123 and harmless_word"
    )
    sanitized = sanitize_error_message(raw_msg)
    assert "secretpass" not in sanitized
    assert "secret_value" not in sanitized
    assert "xyz123" not in sanitized
    assert "harmless_word" in sanitized
    assert "[REDACTED]" in sanitized

    # 3. EvaluationError auto-sanitizes message
    err = EvaluationError(message=raw_msg)
    assert "secretpass" not in err.message
    assert "secret_value" not in err.message
    assert "xyz123" not in err.message
    assert "secretpass" not in str(err)


# ---------------------------------------------------------------------------
# 10. Deterministic Scoring
# ---------------------------------------------------------------------------


def test_deterministic_scoring_reproducibility() -> None:
    metrics = {
        "f1": EvaluationMetric(
            metric_name="f1",
            evaluator_type=EvaluatorType.CONSISTENCY,
            score=0.7142857,
            status=MetricStatus.SUCCESS,
        ),
        "f2": EvaluationMetric(
            metric_name="f2",
            evaluator_type=EvaluatorType.RELEVANCE,
            score=0.8571428,
            status=MetricStatus.SUCCESS,
        ),
    }

    results = [calculate_case_score(metrics)[0] for _ in range(100)]
    # All 100 iterations must yield the exact same score
    assert len(set(results)) == 1
    assert results[0] == 0.785714


# ---------------------------------------------------------------------------
# 11. Evaluation Engine Orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluation_engine_case_and_run_evaluation() -> None:
    engine = EvaluationEngine()

    match_eval = DeterministicMatchEvaluator(
        name="exact_match",
        evaluator_type=EvaluatorType.FACTUALITY,
        threshold=0.9,
    )
    engine.register_evaluator(match_eval)
    assert engine.list_evaluators() == ["exact_match"]
    assert engine.get_evaluator("exact_match") is match_eval

    # Evaluate single case
    case_input = EvaluationInput.create(
        input="What is the capital of Japan?",
        response="Tokyo",
        expected_output="Tokyo",
    )
    case_res = await engine.evaluate_case(case_input)

    assert case_res.overall_score == 1.0
    assert case_res.passed is True
    assert "exact_match" in case_res.metrics
    assert case_res.metrics["exact_match"].status == MetricStatus.SUCCESS

    # Evaluate complete run with multiple cases
    cases = [
        EvaluationInput.create(
            input="Q1", response="Correct", expected_output="Correct"
        ),
        EvaluationInput.create(input="Q2", response="Wrong", expected_output="Correct"),
    ]
    run_id = uuid.uuid4()
    run_res = await engine.evaluate_run(cases, run_id=run_id)

    assert run_res.run_id == run_id
    assert run_res.total_cases == 2
    assert run_res.completed_cases == 1
    assert run_res.failed_cases == 1
    assert run_res.overall_score == 0.5
    assert run_res.metric_averages["exact_match"] == 0.5


@pytest.mark.asyncio
async def test_evaluation_engine_error_containment() -> None:
    engine = EvaluationEngine()
    working_eval = DeterministicMatchEvaluator(
        name="working",
        evaluator_type=EvaluatorType.FACTUALITY,
    )
    failing_eval = FailingEvaluator(
        name="failing",
        evaluator_type=EvaluatorType.RELEVANCE,
    )
    engine.register_evaluator(working_eval)
    engine.register_evaluator(failing_eval)

    case_input = EvaluationInput.create(
        input="2 + 2",
        response="4",
        expected_output="4",
    )
    case_res = await engine.evaluate_case(case_input)

    # Working evaluator succeeded
    assert case_res.metrics["working"].score == 1.0
    assert case_res.metrics["working"].status == MetricStatus.SUCCESS

    # Failing evaluator was trapped cleanly without aborting the engine
    assert case_res.metrics["failing"].score is None
    assert case_res.metrics["failing"].status == MetricStatus.ERROR
    assert len(case_res.errors) == 1
    assert "failing" in case_res.errors[0]
    assert case_res.passed is False


def test_score_direction_enum() -> None:
    assert ScoreDirection.HIGHER_IS_BETTER == "higher_is_better"
    assert ScoreDirection.LOWER_IS_BETTER == "lower_is_better"
