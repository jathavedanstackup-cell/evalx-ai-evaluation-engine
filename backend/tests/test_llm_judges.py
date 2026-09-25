from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.evaluation.engine import EvaluationEngine
from app.evaluation.enums import EvaluatorType, MetricStatus
from app.evaluation.errors import (
    EvaluationTimeoutError,
    EvaluatorExecutionError,
    MalformedEvaluatorOutputError,
)
from app.evaluation.evaluators import (
    ConsistencyEvaluator,
    ExactMatchEvaluator,
    FactualityEvaluator,
    FaithfulnessEvaluator,
    HallucinationEvaluator,
    RelevanceEvaluator,
)
from app.evaluation.judges import (
    BaseLLMJudge,
    JudgeRequest,
    JudgeResponse,
    LiteLLMJudge,
)
from app.evaluation.registry import EvaluatorRegistry
from app.evaluation.types import EvaluationInput, ModelInfo


class DummyMockJudge(BaseLLMJudge):
    """Simple in-memory test judge conforming to BaseLLMJudge protocol."""

    def __init__(
        self,
        score: float = 1.0,
        rationale: str = "Looks accurate.",
        evidence: list[str] | None = None,
        claims: list[str] | None = None,
        model_name: str = "mock-judge",
    ) -> None:
        self._score = score
        self._rationale = rationale
        self._evidence = evidence or []
        self._claims = claims or []
        self._model_name = model_name
        self.recorded_requests: list[JudgeRequest] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    async def judge(self, request: JudgeRequest) -> JudgeResponse:
        self.recorded_requests.append(request)
        return JudgeResponse(
            score=self._score,
            passed=self._score >= 0.8,
            rationale=self._rationale,
            evidence=self._evidence,
            claims=self._claims,
            metadata={"model": self._model_name},
        )


# ===========================================================================
# 1. Judge Contract & Protocol Tests
# ===========================================================================


def test_judge_request_valid() -> None:
    req = JudgeRequest(
        prompt="Explain quantum entanglement",
        response="Entanglement is a physical phenomenon...",
        reference="Quantum entanglement occurs when...",
        context=["Passage 1", "Passage 2"],
        rubric="Score 1.0 for accurate description",
        metadata={"tag": "physics"},
        model_info=ModelInfo(provider="openai", model_name="gpt-4o"),
    )
    assert req.prompt == "Explain quantum entanglement"
    assert req.response.startswith("Entanglement")
    assert req.reference is not None
    assert isinstance(req.context, list)
    assert req.rubric == "Score 1.0 for accurate description"
    assert req.metadata["tag"] == "physics"
    assert req.model_info is not None and req.model_info.model_name == "gpt-4o"


def test_judge_request_blank_prompt_or_response_fails() -> None:
    with pytest.raises(ValidationError):
        JudgeRequest(
            prompt="",
            response="Answer",
            rubric="Rubric",
        )

    with pytest.raises(ValidationError):
        JudgeRequest(
            prompt="Question",
            response="",
            rubric="Rubric",
        )


def test_judge_response_defaults_and_validation() -> None:
    resp = JudgeResponse(score=0.92)
    assert resp.score == 0.92
    assert resp.passed is None
    assert resp.rationale == ""
    assert resp.evidence == []
    assert resp.claims == []
    assert resp.metadata == {}
    assert resp.raw_response is None

    # Score out of bounds
    with pytest.raises(ValidationError):
        JudgeResponse(score=1.5)

    with pytest.raises(ValidationError):
        JudgeResponse(score=-0.1)


def test_base_llm_judge_protocol_compliance() -> None:
    dummy = DummyMockJudge()
    assert isinstance(dummy, BaseLLMJudge)
    assert dummy.model_name == "mock-judge"


# ===========================================================================
# 2. LiteLLMJudge Adapter Tests (Mocked - Zero Network Calls)
# ===========================================================================


@pytest.mark.asyncio
async def test_litellm_judge_happy_path() -> None:
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = (
        '{"score": 0.88, "passed": true, "rationale": "High quality answer", '
        '"evidence": ["Quote A"], "claims": ["Claim 1"]}'
    )
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 150
    mock_response.usage.completion_tokens = 45
    mock_response.usage.total_tokens = 195

    judge = LiteLLMJudge(model="gpt-4o-mini", temperature=0.0)

    with patch(
        "app.evaluation.judges.litellm.acompletion", new_callable=AsyncMock
    ) as mock_acompletion:
        mock_acompletion.return_value = mock_response

        request = JudgeRequest(
            prompt="What is photosynthesis?",
            response="Photosynthesis is the process by which plants...",
            reference="Process used by plants to convert light into chemical energy.",
            context=["Biology textbook passage"],
            rubric="Rate factual accuracy from 0.0 to 1.0",
        )

        resp = await judge.judge(request)

        assert resp.score == 0.88
        assert resp.passed is True
        assert resp.rationale == "High quality answer"
        assert resp.evidence == ["Quote A"]
        assert resp.claims == ["Claim 1"]
        assert resp.metadata["model"] == "gpt-4o-mini"
        assert resp.metadata["prompt_tokens"] == 150
        assert resp.metadata["completion_tokens"] == 45
        assert resp.metadata["total_tokens"] == 195
        assert "latency_ms" in resp.metadata
        mock_acompletion.assert_awaited_once()


@pytest.mark.asyncio
async def test_litellm_judge_markdown_fenced_json() -> None:
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = """```json
{
  "score": 0.95,
  "passed": true,
  "rationale": "Fenced markdown parsed properly",
  "evidence": ["Evidence 1"],
  "claims": ["Claim A"]
}
```"""
    mock_response.choices = [mock_choice]
    mock_response.usage = None

    judge = LiteLLMJudge(model="claude-3-5-sonnet")

    with patch(
        "app.evaluation.judges.litellm.acompletion", new_callable=AsyncMock
    ) as mock_acompletion:
        mock_acompletion.return_value = mock_response

        request = JudgeRequest(
            prompt="Q",
            response="A",
            rubric="Rubric",
        )

        resp = await judge.judge(request)
        assert resp.score == 0.95
        assert resp.rationale == "Fenced markdown parsed properly"
        assert resp.evidence == ["Evidence 1"]


@pytest.mark.asyncio
async def test_litellm_judge_json_with_surrounding_text() -> None:
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = (
        "Here is the evaluation:\n\n"
        '{"score": 0.72, "rationale": "Partially accurate"}\n\n'
        "Hope this helps!"
    )
    mock_response.choices = [mock_choice]
    mock_response.usage = None

    judge = LiteLLMJudge(model="gpt-4o-mini")

    with patch(
        "app.evaluation.judges.litellm.acompletion", new_callable=AsyncMock
    ) as mock_acompletion:
        mock_acompletion.return_value = mock_response

        request = JudgeRequest(
            prompt="Q",
            response="A",
            rubric="Rubric",
        )

        resp = await judge.judge(request)
        assert resp.score == 0.72
        assert resp.rationale == "Partially accurate"


@pytest.mark.asyncio
async def test_litellm_judge_score_clamping() -> None:
    # Test score above 1.0 clamped to 1.0
    mock_response_high = MagicMock()
    mock_choice_high = MagicMock()
    mock_choice_high.message.content = '{"score": 1.45, "rationale": "Over 1.0"}'
    mock_response_high.choices = [mock_choice_high]
    mock_response_high.usage = None

    judge = LiteLLMJudge(model="gpt-4o-mini")

    with patch(
        "app.evaluation.judges.litellm.acompletion", new_callable=AsyncMock
    ) as mock_acompletion:
        mock_acompletion.return_value = mock_response_high
        resp = await judge.judge(JudgeRequest(prompt="Q", response="A", rubric="R"))
        assert resp.score == 1.0

    # Test score below 0.0 clamped to 0.0
    mock_response_low = MagicMock()
    mock_choice_low = MagicMock()
    mock_choice_low.message.content = '{"score": -0.5, "rationale": "Below 0"}'
    mock_response_low.choices = [mock_choice_low]
    mock_response_low.usage = None

    with patch(
        "app.evaluation.judges.litellm.acompletion", new_callable=AsyncMock
    ) as mock_acompletion:
        mock_acompletion.return_value = mock_response_low
        resp = await judge.judge(JudgeRequest(prompt="Q", response="A", rubric="R"))
        assert resp.score == 0.0


@pytest.mark.asyncio
async def test_litellm_judge_missing_score_raises_malformed() -> None:
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '{"rationale": "Missing score field entirely"}'
    mock_response.choices = [mock_choice]
    mock_response.usage = None

    judge = LiteLLMJudge(model="gpt-4o-mini")

    with patch(
        "app.evaluation.judges.litellm.acompletion", new_callable=AsyncMock
    ) as mock_acompletion:
        mock_acompletion.return_value = mock_response

        with pytest.raises(
            MalformedEvaluatorOutputError, match="missing required 'score'"
        ):
            await judge.judge(JudgeRequest(prompt="Q", response="A", rubric="R"))


@pytest.mark.asyncio
async def test_litellm_judge_non_numeric_score_raises_malformed() -> None:
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '{"score": "excellent", "rationale": "Good"}'
    mock_response.choices = [mock_choice]
    mock_response.usage = None

    judge = LiteLLMJudge(model="gpt-4o-mini")

    with patch(
        "app.evaluation.judges.litellm.acompletion", new_callable=AsyncMock
    ) as mock_acompletion:
        mock_acompletion.return_value = mock_response

        with pytest.raises(MalformedEvaluatorOutputError, match="must be numeric"):
            await judge.judge(JudgeRequest(prompt="Q", response="A", rubric="R"))


@pytest.mark.asyncio
async def test_litellm_judge_invalid_json_raises_malformed() -> None:
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Internal 500 server error from upstream API"
    mock_response.choices = [mock_choice]
    mock_response.usage = None

    judge = LiteLLMJudge(model="gpt-4o-mini")

    with patch(
        "app.evaluation.judges.litellm.acompletion", new_callable=AsyncMock
    ) as mock_acompletion:
        mock_acompletion.return_value = mock_response

        with pytest.raises(MalformedEvaluatorOutputError, match="could not be parsed"):
            await judge.judge(JudgeRequest(prompt="Q", response="A", rubric="R"))


@pytest.mark.asyncio
async def test_litellm_judge_timeout_error_translation() -> None:
    judge = LiteLLMJudge(model="gpt-4o-mini", timeout=10.0)

    with patch(
        "app.evaluation.judges.litellm.acompletion", new_callable=AsyncMock
    ) as mock_acompletion:
        mock_acompletion.side_effect = TimeoutError("Call timed out")

        with pytest.raises(EvaluationTimeoutError, match="timed out after 10.0s"):
            await judge.judge(JudgeRequest(prompt="Q", response="A", rubric="R"))


@pytest.mark.asyncio
async def test_litellm_judge_credential_sanitization_on_error() -> None:
    api_key_secret = "sk-super-secret-key-1234567890"
    judge = LiteLLMJudge(
        model="gpt-4o-mini",
        api_key=api_key_secret,
    )

    with patch(
        "app.evaluation.judges.litellm.acompletion", new_callable=AsyncMock
    ) as mock_acompletion:
        mock_acompletion.side_effect = RuntimeError(
            f"Authentication failed for key {api_key_secret} and Bearer xyz987654321"
        )

        with pytest.raises(EvaluatorExecutionError) as exc_info:
            await judge.judge(JudgeRequest(prompt="Q", response="A", rubric="R"))

        err_dict = exc_info.value.to_dict()
        err_str = str(exc_info.value)

        # Assert cleartext secret never leaked
        assert api_key_secret not in err_str
        assert "xyz987654321" not in err_str
        assert api_key_secret not in str(err_dict)
        assert "[REDACTED]" in err_dict["details"]["error"]


@pytest.mark.asyncio
async def test_litellm_judge_context_formatting() -> None:
    judge = LiteLLMJudge()

    with patch(
        "app.evaluation.judges.litellm.acompletion", new_callable=AsyncMock
    ) as mock_acompletion:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"score": 1.0}'))]
        mock_response.usage = None
        mock_acompletion.return_value = mock_response

        # List context
        req_list = JudgeRequest(
            prompt="Q",
            response="A",
            context=["First paragraph", "Second paragraph"],
            rubric="R",
        )
        await judge.judge(req_list)
        call_args = mock_acompletion.call_args[1]
        user_content = call_args["messages"][1]["content"]
        assert "[1] First paragraph" in user_content
        assert "[2] Second paragraph" in user_content

        # Dict context
        req_dict = JudgeRequest(
            prompt="Q",
            response="A",
            context={"doc_id": "123", "text": "sample"},
            rubric="R",
        )
        await judge.judge(req_dict)
        call_args2 = mock_acompletion.call_args[1]
        user_content2 = call_args2["messages"][1]["content"]
        assert '"doc_id": "123"' in user_content2


# ===========================================================================
# 3. FactualityEvaluator Tests (with Injected Judge)
# ===========================================================================


@pytest.mark.asyncio
async def test_factuality_evaluator_with_expected_output() -> None:
    mock_judge = DummyMockJudge(
        score=0.9,
        rationale="Matches reference factual truth.",
        evidence=["Fact A confirmed"],
        claims=["Claim A"],
    )
    evaluator = FactualityEvaluator(threshold=0.8, judge=mock_judge)

    input_data = EvaluationInput.create(
        input="Capital of Australia?",
        response="Canberra is the capital of Australia.",
        expected_output="Canberra",
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.evaluator_type == EvaluatorType.FACTUALITY
    assert metric.score == 0.9
    assert metric.passed is True
    assert metric.status == MetricStatus.SUCCESS
    assert metric.explanation == "Matches reference factual truth."
    assert metric.metadata["evidence"] == ["Fact A confirmed"]
    assert metric.metadata["claims"] == ["Claim A"]
    assert metric.metadata["judge_model"] == "mock-judge"
    assert len(mock_judge.recorded_requests) == 1
    assert mock_judge.recorded_requests[0].reference == "Canberra"


@pytest.mark.asyncio
async def test_factuality_evaluator_with_context() -> None:
    mock_judge = DummyMockJudge(score=0.85, rationale="Supported by context")
    evaluator = FactualityEvaluator(judge=mock_judge)

    input_data = EvaluationInput.create(
        input="What is the speed limit?",
        response="The speed limit is 55 mph.",
        context=["Section 4: The speed limit on this road is 55 mph."],
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.score == 0.85
    assert metric.passed is True
    assert len(mock_judge.recorded_requests) == 1
    assert mock_judge.recorded_requests[0].context == [
        "Section 4: The speed limit on this road is 55 mph."
    ]


@pytest.mark.asyncio
async def test_factuality_evaluator_missing_reference_returns_unavailable() -> None:
    mock_judge = DummyMockJudge()
    evaluator = FactualityEvaluator(judge=mock_judge)

    # Neither expected_output nor context provided
    input_data = EvaluationInput.create(
        input="Capital of Australia?",
        response="Canberra is the capital.",
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.score is None
    assert metric.passed is None
    assert metric.status == MetricStatus.UNAVAILABLE
    assert "requires expected_output or retrieved_context" in (metric.explanation or "")
    # Ensure judge was never invoked when prerequisites are missing
    assert len(mock_judge.recorded_requests) == 0


@pytest.mark.asyncio
async def test_factuality_evaluator_threshold_failed() -> None:
    mock_judge = DummyMockJudge(score=0.4, rationale="Contradicts reference")
    evaluator = FactualityEvaluator(threshold=0.8, judge=mock_judge)

    input_data = EvaluationInput.create(
        input="Capital of Australia?",
        response="Sydney is the capital of Australia.",
        expected_output="Canberra",
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.score == 0.4
    assert metric.passed is False
    assert metric.status == MetricStatus.THRESHOLD_FAILED


# ===========================================================================
# 4. RelevanceEvaluator Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_relevance_evaluator_success() -> None:
    mock_judge = DummyMockJudge(
        score=0.95,
        rationale="Response directly answers question.",
    )
    evaluator = RelevanceEvaluator(threshold=0.8, judge=mock_judge)

    input_data = EvaluationInput.create(
        input="How to reset password?",
        response="Navigate to Settings -> Account -> Reset Password.",
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.evaluator_type == EvaluatorType.RELEVANCE
    assert metric.score == 0.95
    assert metric.passed is True
    assert metric.status == MetricStatus.SUCCESS
    assert len(mock_judge.recorded_requests) == 1
    assert mock_judge.recorded_requests[0].prompt == "How to reset password?"


@pytest.mark.asyncio
async def test_relevance_evaluator_irrelevant_response() -> None:
    mock_judge = DummyMockJudge(
        score=0.1,
        rationale="Response discusses weather instead of password reset.",
    )
    evaluator = RelevanceEvaluator(threshold=0.8, judge=mock_judge)

    input_data = EvaluationInput.create(
        input="How to reset password?",
        response="Today the weather in California is sunny and warm.",
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.score == 0.1
    assert metric.passed is False
    assert metric.status == MetricStatus.THRESHOLD_FAILED


# ===========================================================================
# 5. FaithfulnessEvaluator Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_faithfulness_evaluator_with_context() -> None:
    mock_judge = DummyMockJudge(
        score=1.0,
        rationale="100% faithful to retrieved passages.",
        evidence=["Document quote"],
    )
    evaluator = FaithfulnessEvaluator(threshold=0.85, judge=mock_judge)

    input_data = EvaluationInput.create(
        input="What is the company refund policy?",
        response="Customers can request a refund within 30 days.",
        context=["Refunds are accepted within 30 days of purchase with receipt."],
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.evaluator_type == EvaluatorType.FAITHFULNESS
    assert metric.score == 1.0
    assert metric.passed is True
    assert metric.status == MetricStatus.SUCCESS
    assert metric.metadata["evidence"] == ["Document quote"]
    assert len(mock_judge.recorded_requests) == 1


@pytest.mark.asyncio
async def test_faithfulness_evaluator_missing_context_returns_unavailable() -> None:
    mock_judge = DummyMockJudge()
    evaluator = FaithfulnessEvaluator(judge=mock_judge)

    # No context provided
    input_data = EvaluationInput.create(
        input="Refund policy?",
        response="Refunds are within 30 days.",
        expected_output="Refunds are 30 days.",
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.score is None
    assert metric.passed is None
    assert metric.status == MetricStatus.UNAVAILABLE
    assert "requires retrieved context passages" in (metric.explanation or "")
    assert len(mock_judge.recorded_requests) == 0


@pytest.mark.asyncio
async def test_faithfulness_evaluator_empty_context_list_returns_unavailable() -> None:
    mock_judge = DummyMockJudge()
    evaluator = FaithfulnessEvaluator(judge=mock_judge)

    input_data = EvaluationInput.create(
        input="Refund policy?",
        response="Refunds are within 30 days.",
        context=[],
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.score is None
    assert metric.status == MetricStatus.UNAVAILABLE
    assert "non-empty" in (metric.explanation or "")
    assert len(mock_judge.recorded_requests) == 0


# ===========================================================================
# 6. HallucinationEvaluator Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_hallucination_evaluator_no_hallucination_score_1() -> None:
    # Score 1.0 = Clean / No hallucination detected (EVALX standard: higher is better)
    mock_judge = DummyMockJudge(
        score=1.0,
        rationale="No hallucination detected; all statements verified.",
    )
    evaluator = HallucinationEvaluator(threshold=0.8, judge=mock_judge)

    input_data = EvaluationInput.create(
        input="Where is the Eiffel Tower?",
        response="The Eiffel Tower is in Paris, France.",
        expected_output="Paris, France",
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.evaluator_type == EvaluatorType.HALLUCINATION
    assert metric.score == 1.0
    assert metric.passed is True
    assert metric.status == MetricStatus.SUCCESS


@pytest.mark.asyncio
async def test_hallucination_evaluator_severe_hallucination_score_low() -> None:
    # Score 0.1 = Severe hallucination detected -> fails threshold
    mock_judge = DummyMockJudge(
        score=0.1,
        rationale=(
            "Fabricated claims: Eiffel Tower is not made of pure chocolate in Berlin."
        ),
    )
    evaluator = HallucinationEvaluator(threshold=0.8, judge=mock_judge)

    input_data = EvaluationInput.create(
        input="Where is the Eiffel Tower?",
        response="The Eiffel Tower is made of pure chocolate and located in Berlin.",
        expected_output="Paris, France",
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.score == 0.1
    assert metric.passed is False
    assert metric.status == MetricStatus.THRESHOLD_FAILED


@pytest.mark.asyncio
async def test_hallucination_evaluator_missing_reference_returns_unavailable() -> None:
    mock_judge = DummyMockJudge()
    evaluator = HallucinationEvaluator(judge=mock_judge)

    input_data = EvaluationInput.create(
        input="Question",
        response="Answer",
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.score is None
    assert metric.passed is None
    assert metric.status == MetricStatus.UNAVAILABLE
    assert "requires expected_output or retrieved_context" in (metric.explanation or "")
    assert len(mock_judge.recorded_requests) == 0


# ===========================================================================
# 7. ConsistencyEvaluator Stub Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_consistency_evaluator_single_response_unavailable() -> None:
    evaluator = ConsistencyEvaluator()
    input_data = EvaluationInput.create(input="Q", response="A")
    metric = await evaluator.evaluate(input_data)
    assert metric.evaluator_type == EvaluatorType.CONSISTENCY
    assert metric.score is None
    assert metric.passed is None
    assert metric.status == MetricStatus.UNAVAILABLE
    assert "requires at least 2 candidate responses" in (metric.explanation or "")


# ===========================================================================
# 8. Engine & Registry Integration Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_engine_mixed_deterministic_and_llm_judge_evaluators() -> None:
    judge_fact = DummyMockJudge(score=1.0, rationale="Factual ground truth confirmed.")
    judge_rel = DummyMockJudge(score=0.9, rationale="Highly relevant response.")

    registry = EvaluatorRegistry()
    registry.register(ExactMatchEvaluator(name="exact_match"))
    registry.register(FactualityEvaluator(name="factuality", judge=judge_fact))
    registry.register(RelevanceEvaluator(name="relevance", judge=judge_rel))

    engine = EvaluationEngine(registry=registry)

    input_data = EvaluationInput.create(
        input="What is the capital of France?",
        response="Paris",
        expected_output="Paris",
    )

    case_result = await engine.evaluate_case(input_data)

    assert case_result.passed is True
    assert case_result.overall_score is not None
    assert case_result.overall_score >= 0.95
    assert len(case_result.metrics) == 3

    assert case_result.metrics["exact_match"].score == 1.0
    assert case_result.metrics["factuality"].score == 1.0
    assert case_result.metrics["relevance"].score == 0.9
    assert len(case_result.errors) == 0


@pytest.mark.asyncio
async def test_engine_judge_evaluator_timeout_containment() -> None:
    class TimingOutJudge(BaseLLMJudge):
        @property
        def model_name(self) -> str:
            return "timeout-judge"

        async def judge(self, request: JudgeRequest) -> JudgeResponse:
            raise EvaluationTimeoutError("Judge request timed out after 30s")

    registry = EvaluatorRegistry()
    registry.register(ExactMatchEvaluator(name="exact_match"))
    registry.register(FactualityEvaluator(name="factuality", judge=TimingOutJudge()))

    engine = EvaluationEngine(registry=registry)

    input_data = EvaluationInput.create(
        input="City?",
        response="Tokyo",
        expected_output="Tokyo",
    )

    case_result = await engine.evaluate_case(input_data)

    # Exact match succeeds
    assert case_result.metrics["exact_match"].score == 1.0
    assert case_result.metrics["exact_match"].passed is True

    # Factuality timed out -> status ERROR, contained
    assert case_result.metrics["factuality"].status == MetricStatus.ERROR
    assert len(case_result.errors) == 1
    assert "timed out" in case_result.errors[0]
