import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.v1.evaluation_runs import get_run_executor
from app.auth.dependencies import get_auth_provider
from app.auth.test_provider import TestAuthProvider
from app.core.config import require_test_database_url, settings
from app.database.session import get_async_session
from app.evaluation.engine import EvaluationEngine
from app.evaluation.enums import EvaluatorBackend, EvaluatorType, MetricStatus
from app.evaluation.errors import (
    EvaluationTimeoutError,
    EvaluatorExecutionError,
    MalformedEvaluatorOutputError,
)
from app.evaluation.evaluators import ConsistencyEvaluator
from app.evaluation.evaluators.reference_aware import DEFAULT_CONSISTENCY_RUBRIC
from app.evaluation.factory import create_evaluator
from app.evaluation.judges import (
    BaseLLMJudge,
    ConsistencyJudgeRequest,
    JudgeRequest,
    JudgeResponse,
    LiteLLMJudge,
)
from app.evaluation.scoring import calculate_case_score, calculate_run_score
from app.evaluation.types import (
    EvaluationCaseResult,
    EvaluationContext,
    EvaluationInput,
    EvaluationMetric,
)
from app.main import app
from app.services.run_executor import SynchronousRunExecutor


class MockConsistencyJudge(BaseLLMJudge):
    """Deterministic in-memory judge conforming to BaseLLMJudge protocol."""

    def __init__(
        self,
        score: float = 1.0,
        rationale: str = "Candidate responses are completely consistent.",
        evidence: list[str] | None = None,
        claims: list[str] | None = None,
        model_name: str = "mock-consistency-judge",
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
# 1. Contract & Factory Tests
# ===========================================================================


def test_consistency_evaluator_registration_and_properties() -> None:
    judge = MockConsistencyJudge(score=0.95)
    evaluator = ConsistencyEvaluator(
        name="custom_consistency",
        threshold=0.85,
        judge=judge,
        config={"rubric": "Custom consistency rubric"},
    )
    assert evaluator.name == "custom_consistency"
    assert evaluator.evaluator_type == EvaluatorType.CONSISTENCY
    assert evaluator.threshold == 0.85
    assert evaluator.config == {"rubric": "Custom consistency rubric"}
    assert evaluator.judge is judge


def test_factory_creates_consistency_evaluator() -> None:
    evaluator = create_evaluator(
        evaluator_type="consistency",
        backend=EvaluatorBackend.LLM_JUDGE,
        threshold=0.75,
    )
    assert isinstance(evaluator, ConsistencyEvaluator)
    assert evaluator.evaluator_type == EvaluatorType.CONSISTENCY
    assert evaluator.threshold == 0.75


def test_engine_registers_and_retrieves_consistency_evaluator() -> None:
    engine = EvaluationEngine()
    judge = MockConsistencyJudge()
    evaluator = ConsistencyEvaluator(judge=judge)
    engine.register_evaluator(evaluator)

    retrieved = engine.get_evaluator("consistency")
    assert retrieved is evaluator
    by_type = engine.get_evaluators_by_type(EvaluatorType.CONSISTENCY)
    assert len(by_type) == 1
    assert by_type[0] is evaluator


# ===========================================================================
# 2. Multi-Response Semantics Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_consistency_two_consistent_responses() -> None:
    judge = MockConsistencyJudge(
        score=1.0,
        rationale="Both candidates state that Paris is the capital.",
        evidence=["Paris is the capital", "The French capital is Paris"],
    )
    evaluator = ConsistencyEvaluator(judge=judge, threshold=0.8)

    input_data = EvaluationInput.create(
        input="What is the capital of France?",
        candidate_responses=[
            "Paris is the capital of France.",
            "The capital city of France is Paris.",
        ],
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.evaluator_type == EvaluatorType.CONSISTENCY
    assert metric.score == 1.0
    assert metric.passed is True
    assert metric.status == MetricStatus.SUCCESS
    assert "Paris" in (metric.explanation or "")
    assert metric.metadata is not None
    assert metric.metadata.get("candidate_count") == 2

    # Verify request was correctly submitted to judge
    assert len(judge.recorded_requests) == 1
    req = judge.recorded_requests[0]
    assert req.prompt == "What is the capital of France?"
    assert req.candidate_responses == [
        "Paris is the capital of France.",
        "The capital city of France is Paris.",
    ]


@pytest.mark.asyncio
async def test_consistency_two_contradictory_responses() -> None:
    judge = MockConsistencyJudge(
        score=0.0,
        rationale="Direct factual contradiction: one says safe, the other says unsafe.",
        evidence=["Drug is safe", "Drug is unsafe and prohibited"],
    )
    evaluator = ConsistencyEvaluator(judge=judge, threshold=0.8)

    input_data = EvaluationInput.create(
        input="Is the medication approved?",
        candidate_responses=[
            "The medication is approved and safe.",
            "The medication is unsafe and strictly prohibited.",
        ],
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.score == 0.0
    assert metric.passed is False
    assert metric.status == MetricStatus.THRESHOLD_FAILED


@pytest.mark.asyncio
async def test_consistency_three_or_more_responses() -> None:
    judge = MockConsistencyJudge(score=0.85)
    evaluator = ConsistencyEvaluator(judge=judge)

    candidates = [
        "Light travels at 300,000 km/s.",
        "The speed of light in vacuum is approximately 300,000 kilometers per second.",
        "In a vacuum, light moves at about 3e8 meters per second.",
    ]
    input_data = EvaluationInput.create(
        input="What is the speed of light?",
        candidate_responses=candidates,
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.score == 0.85
    assert metric.metadata is not None
    assert metric.metadata["candidate_count"] == 3
    assert judge.recorded_requests[0].candidate_responses == candidates


@pytest.mark.asyncio
async def test_consistency_missing_candidate_responses_returns_unavailable() -> None:
    evaluator = ConsistencyEvaluator()
    # Only single response provided without candidate_responses
    input_data = EvaluationInput.create(
        input="What is the speed of light?",
        response="Light travels at 300,000 km/s.",
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.score is None
    assert metric.passed is None
    assert metric.status == MetricStatus.UNAVAILABLE
    assert "requires at least 2 candidate responses" in (metric.explanation or "")


@pytest.mark.asyncio
async def test_consistency_only_one_candidate_response_returns_unavailable() -> None:
    evaluator = ConsistencyEvaluator()
    ctx = EvaluationContext(
        input="What is quantum entanglement?",
        candidate_responses=["Quantum entanglement is a physical phenomenon."],
    )
    input_data = EvaluationInput(
        response="Quantum entanglement is a physical phenomenon.",
        candidate_responses=["Quantum entanglement is a physical phenomenon."],
        context=ctx,
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.score is None
    assert metric.passed is None
    assert metric.status == MetricStatus.UNAVAILABLE
    assert "requires at least 2 candidate responses" in (metric.explanation or "")


@pytest.mark.asyncio
async def test_consistency_empty_or_blank_candidate_response_returns_unavailable() -> (
    None
):
    evaluator = ConsistencyEvaluator()
    input_data = EvaluationInput.create(
        input="Summarize the theory of relativity.",
        candidate_responses=["Einstein published it in 1905.", "   "],
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.score is None
    assert metric.passed is None
    assert metric.status == MetricStatus.UNAVAILABLE
    assert "cannot be empty or whitespace only" in (metric.explanation or "")


@pytest.mark.asyncio
async def test_consistency_candidate_responses_via_metadata() -> None:
    judge = MockConsistencyJudge(score=0.92)
    evaluator = ConsistencyEvaluator(judge=judge)

    # Candidate responses provided through context.metadata
    input_data = EvaluationInput.create(
        input="Define photosynthesis.",
        response="Process by plants.",
        metadata={
            "candidate_responses": [
                "Photosynthesis converts light into chemical energy.",
                "Plants use sunlight to produce glucose.",
            ]
        },
    )

    metric = await evaluator.evaluate(input_data)
    assert metric.score == 0.92
    assert metric.status == MetricStatus.SUCCESS
    assert metric.metadata is not None
    assert metric.metadata["candidate_count"] == 2


# ===========================================================================
# 3. LLM Judge Integration & Prompt Contract Tests
# ===========================================================================


def test_litellm_builds_consistency_messages_correctly() -> None:
    judge = LiteLLMJudge()
    request = JudgeRequest(
        prompt="Explain black holes.",
        response="A black hole is a region of spacetime.",
        candidate_responses=[
            "A black hole is a region of spacetime where gravity is strong.",
            "Black holes are spacetime regions where nothing can escape.",
        ],
        rubric=DEFAULT_CONSISTENCY_RUBRIC,
        reference="Gravity prevents anything from escaping.",
    )

    messages = judge._build_messages(request)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "multi-response semantic consistency" in messages[0]["content"]

    user_content = messages[1]["content"]
    assert "### EVALUATION RUBRIC" in user_content
    assert "### SHARED INPUT PROMPT\nExplain black holes." in user_content
    assert (
        "### CANDIDATE RESPONSES TO EVALUATE FOR CONSISTENCY (2 candidates)"
        in user_content
    )
    assert "--- CANDIDATE RESPONSE 1 ---" in user_content
    assert "--- CANDIDATE RESPONSE 2 ---" in user_content
    assert "### REFERENCE GROUND TRUTH (OPTIONAL)" in user_content


def test_consistency_judge_request_conversion() -> None:
    req = ConsistencyJudgeRequest(
        prompt="Explain Newton's third law.",
        candidate_responses=[
            "Every action has an equal and opposite reaction.",
            "For every action force, there is a reaction force of equal magnitude.",
        ],
        rubric="Test rubric",
    )
    judge_req = req.to_judge_request()
    assert judge_req.prompt == req.prompt
    assert judge_req.candidate_responses == req.candidate_responses
    assert judge_req.response == req.candidate_responses[0]
    assert judge_req.rubric == req.rubric


@pytest.mark.asyncio
async def test_judge_score_clamping() -> None:
    judge = LiteLLMJudge()
    with patch(
        "app.evaluation.judges.litellm.acompletion", new_callable=AsyncMock
    ) as mock_acompletion:
        # Simulate LLM returning score > 1.0
        mock_response = AsyncMock()
        mock_response.choices = [
            AsyncMock(
                message=AsyncMock(
                    content='{"score": 1.5, "passed": true, "rationale": "Overclamped"}'
                )
            )
        ]
        mock_acompletion.return_value = mock_response

        res = await judge.judge(
            JudgeRequest(
                prompt="Q",
                response="R1",
                candidate_responses=["R1", "R2"],
                rubric="Rubric",
            )
        )
        assert res.score == 1.0


@pytest.mark.asyncio
async def test_judge_timeout_raises_evaluation_timeout_error() -> None:
    judge = LiteLLMJudge(timeout=0.01)
    with patch(
        "app.evaluation.judges.litellm.acompletion",
        side_effect=TimeoutError("Timed out"),
    ):
        with pytest.raises(EvaluationTimeoutError):
            await judge.judge(
                JudgeRequest(
                    prompt="Q",
                    response="R1",
                    candidate_responses=["R1", "R2"],
                    rubric="Rubric",
                )
            )


@pytest.mark.asyncio
async def test_judge_malformed_json_raises_malformed_output_error() -> None:
    judge = LiteLLMJudge()
    with patch(
        "app.evaluation.judges.litellm.acompletion", new_callable=AsyncMock
    ) as mock_acompletion:
        mock_response = AsyncMock()
        mock_response.choices = [
            AsyncMock(message=AsyncMock(content="This is not valid JSON at all."))
        ]
        mock_acompletion.return_value = mock_response

        with pytest.raises(MalformedEvaluatorOutputError):
            await judge.judge(
                JudgeRequest(
                    prompt="Q",
                    response="R1",
                    candidate_responses=["R1", "R2"],
                    rubric="Rubric",
                )
            )


@pytest.mark.asyncio
async def test_judge_provider_failure_sanitization() -> None:
    judge = LiteLLMJudge(api_key="secret-api-key-xyz")
    with patch(
        "app.evaluation.judges.litellm.acompletion",
        side_effect=RuntimeError("Connection refused to https://api.openai.com"),
    ):
        with pytest.raises(EvaluatorExecutionError) as exc_info:
            await judge.judge(
                JudgeRequest(
                    prompt="Q",
                    response="R1",
                    candidate_responses=["R1", "R2"],
                    rubric="Rubric",
                )
            )
        err_msg = str(exc_info.value)
        assert "secret-api-key-xyz" not in err_msg


# ===========================================================================
# 4. Aggregation Tests
# ===========================================================================


def test_case_score_aggregation_with_consistency() -> None:
    m_fact = EvaluationMetric(
        metric_name="factuality",
        evaluator_type=EvaluatorType.FACTUALITY,
        score=0.9,
        passed=True,
        threshold=0.8,
        status=MetricStatus.SUCCESS,
    )
    m_cons = EvaluationMetric(
        metric_name="consistency",
        evaluator_type=EvaluatorType.CONSISTENCY,
        score=1.0,
        passed=True,
        threshold=0.8,
        status=MetricStatus.SUCCESS,
    )

    overall_score, passed = calculate_case_score([m_fact, m_cons])
    assert overall_score == 0.95
    assert passed is True


def test_case_score_excludes_unavailable_consistency() -> None:
    m_fact = EvaluationMetric(
        metric_name="factuality",
        evaluator_type=EvaluatorType.FACTUALITY,
        score=0.8,
        passed=True,
        threshold=0.8,
        status=MetricStatus.SUCCESS,
    )
    m_cons_unavail = EvaluationMetric(
        metric_name="consistency",
        evaluator_type=EvaluatorType.CONSISTENCY,
        score=None,
        passed=None,
        threshold=0.8,
        status=MetricStatus.UNAVAILABLE,
    )

    overall_score, passed = calculate_case_score([m_fact, m_cons_unavail])
    # Denominator only includes factuality
    assert overall_score == 0.8
    assert passed is True


def test_run_score_aggregation_with_consistency() -> None:
    c1 = EvaluationCaseResult(
        case_id=uuid.uuid4(),
        overall_score=0.9,
        passed=True,
        metrics={
            "consistency": EvaluationMetric(
                metric_name="consistency",
                evaluator_type=EvaluatorType.CONSISTENCY,
                score=0.9,
                passed=True,
                status=MetricStatus.SUCCESS,
            )
        },
    )
    c2 = EvaluationCaseResult(
        case_id=uuid.uuid4(),
        overall_score=0.7,
        passed=False,
        metrics={
            "consistency": EvaluationMetric(
                metric_name="consistency",
                evaluator_type=EvaluatorType.CONSISTENCY,
                score=0.7,
                passed=False,
                status=MetricStatus.THRESHOLD_FAILED,
            )
        },
    )

    overall_run, metric_avgs, completed, failed = calculate_run_score([c1, c2])
    assert overall_run == 0.8
    assert metric_avgs["consistency"] == 0.8
    assert completed == 1
    assert failed == 1


# ===========================================================================
# 5. API & End-to-End Execution Tests
# ===========================================================================


@asynccontextmanager
async def managed_auth_client(
    default_user_id: str = "test-consistency-user",
    executor_override: Any = None,
) -> AsyncIterator[AsyncClient]:
    """Test client fixture with clean test-database isolation."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def get_test_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = get_test_db_session
    app.dependency_overrides[get_auth_provider] = lambda: TestAuthProvider(
        default_user_id=default_user_id
    )
    app.dependency_overrides[get_run_executor] = lambda: (
        executor_override or SynchronousRunExecutor()
    )
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(
                text(
                    "TRUNCATE TABLE evaluation_results, evaluation_runs, "
                    "evaluator_configs, evaluations, dataset_cases, datasets, users "
                    "CASCADE;"
                )
            )
            await cleanup_session.commit()
        await engine.dispose()


def make_auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer test-token-{user_id}"}


@pytest.mark.asyncio
async def test_end_to_end_consistency_run_execution() -> None:
    mock_judge = MockConsistencyJudge(
        score=0.95, rationale="High consistency across runs."
    )

    def _mock_create_evaluator(**kw: Any) -> Any:
        if kw.get("evaluator_type") == "consistency":
            return ConsistencyEvaluator(
                name=kw.get("name") or "consistency",
                threshold=kw.get("threshold", 0.8),
                judge=mock_judge,
                config=kw.get("config"),
            )
        return create_evaluator(**kw)

    # Patch create_evaluator to use mock judge for consistency
    with patch(
        "app.services.evaluation_run_service.create_evaluator",
        side_effect=_mock_create_evaluator,
    ):
        async with managed_auth_client(
            default_user_id="user-consistency-e2e"
        ) as client:
            headers = make_auth_headers("user-consistency-e2e")

            # 1. Create dataset
            ds_res = await client.post(
                "/api/v1/datasets",
                json={"name": "Consistency Test Dataset"},
                headers=headers,
            )
            assert ds_res.status_code == 201
            ds_id = ds_res.json()["id"]

            # 2. Create test case
            case_res = await client.post(
                f"/api/v1/datasets/{ds_id}/cases",
                json={
                    "input": "Explain gravity in one sentence.",
                    "expected_output": "Gravity attracts objects with mass.",
                },
                headers=headers,
            )
            assert case_res.status_code == 201
            case_id = case_res.json()["id"]

            # 3. Launch run with multi-response candidate input
            run_payload = {
                "dataset_id": ds_id,
                "name": "Consistency Run E2E",
                "evaluators": [
                    {
                        "evaluator_type": "consistency",
                        "backend": "llm_judge",
                        "threshold": 0.8,
                    }
                ],
                "responses": [
                    {
                        "case_id": case_id,
                        "candidate_responses": [
                            "Gravity curves spacetime to pull objects.",
                            "Gravity is the force attracting bodies with mass.",
                        ],
                    }
                ],
            }

            launch_res = await client.post(
                "/api/v1/evaluations/runs",
                json=run_payload,
                headers=headers,
            )
            assert launch_res.status_code == 201
            run_data = launch_res.json()
            assert run_data["status"] == "completed"
            assert run_data["completed_cases"] == 1
            assert run_data["failed_cases"] == 0
            assert run_data["overall_score"] == 0.95
            assert "metric_averages" in run_data["metrics_summary"]
            assert run_data["metrics_summary"]["metric_averages"]["consistency"] == 0.95

            # 4. Check case results
            run_id = run_data["id"]
            results_res = await client.get(
                f"/api/v1/evaluations/runs/{run_id}/results",
                headers=headers,
            )
            assert results_res.status_code == 200
            results_data = results_res.json()
            assert len(results_data["items"]) == 1
            item = results_data["items"][0]
            assert item["consistency_score"] == 0.95
            assert item["passed"] is True
            assert "consistency" in item["metrics"]


@pytest.mark.asyncio
async def test_consistency_run_cross_tenant_isolation_404() -> None:
    async with managed_auth_client(default_user_id="user-owner") as client:
        owner_headers = make_auth_headers("user-owner")
        alien_headers = make_auth_headers("user-alien")

        # Owner creates dataset
        ds_res = await client.post(
            "/api/v1/datasets",
            json={"name": "Owner Dataset"},
            headers=owner_headers,
        )
        ds_id = ds_res.json()["id"]

        # Alien attempts to launch run on owner's dataset
        run_payload = {
            "dataset_id": ds_id,
            "evaluators": [{"evaluator_type": "consistency"}],
        }
        alien_res = await client.post(
            "/api/v1/evaluations/runs",
            json=run_payload,
            headers=alien_headers,
        )
        assert alien_res.status_code == 404
