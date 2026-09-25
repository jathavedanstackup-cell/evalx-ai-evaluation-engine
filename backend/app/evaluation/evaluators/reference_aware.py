from typing import Any

from app.evaluation.contracts import AbstractEvaluator
from app.evaluation.enums import EvaluatorType
from app.evaluation.judges.contracts import (
    BaseLLMJudge,
    JudgeRequest,
)
from app.evaluation.judges.litellm import LiteLLMJudge
from app.evaluation.types import EvaluationInput


class FactualityEvaluator(AbstractEvaluator):
    """Reference-aware factuality evaluator powered by an LLM Judge.

    Evaluates factual correctness and alignment of candidate responses against
    ground truth reference outputs or retrieved context passages.
    Returns status=UNAVAILABLE if neither expected_output nor context is provided.
    """

    def __init__(
        self,
        name: str = "factuality",
        threshold: float | None = 0.8,
        judge: BaseLLMJudge | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=EvaluatorType.FACTUALITY,
            threshold=threshold,
            config=config,
        )
        self._judge = judge

    @property
    def judge(self) -> BaseLLMJudge:
        if self._judge is None:
            self._judge = LiteLLMJudge()
        return self._judge

    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> (
        tuple[float | None, str | None, float | None]
        | tuple[float | None, str | None, float | None, dict[str, Any] | None]
    ):
        has_expected = bool(
            input_data.expected_output and input_data.expected_output.strip()
        )
        has_context = bool(input_data.retrieved_context)
        if not has_expected and not has_context:
            return (
                None,
                "FactualityEvaluator requires expected_output or retrieved_context "
                "ground truth, but none was provided.",
                None,
            )

        rubric = (
            "You are an expert factuality evaluator. Evaluate the factual "
            "correctness and precision of the candidate response against the "
            "reference ground truth and context. Penalize any factual errors, "
            "false statements, or contradictory assertions. Score 1.0 if "
            "completely factual and accurate; score 0.0 if completely inaccurate "
            "or contradictory."
        )

        request = JudgeRequest(
            prompt=input_data.input,
            response=input_data.response,
            reference=input_data.expected_output,
            context=input_data.retrieved_context,
            rubric=rubric,
            metadata={
                "evaluator": self.name,
                "case_id": (str(input_data.case_id) if input_data.case_id else None),
            },
            model_info=input_data.model_info,
        )

        judge_resp = await self.judge.judge(request)
        metadata = {
            "evidence": judge_resp.evidence,
            "claims": judge_resp.claims,
            "judge_model": judge_resp.metadata.get("model", self.judge.model_name),
            "judge_metadata": judge_resp.metadata,
        }
        return (judge_resp.score, judge_resp.rationale, None, metadata)


class RelevanceEvaluator(AbstractEvaluator):
    """Query relevance evaluator powered by an LLM Judge.

    Evaluates whether candidate responses directly, specifically, and
    comprehensively address the user query without evasion or tangents.
    """

    def __init__(
        self,
        name: str = "relevance",
        threshold: float | None = 0.8,
        judge: BaseLLMJudge | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=EvaluatorType.RELEVANCE,
            threshold=threshold,
            config=config,
        )
        self._judge = judge

    @property
    def judge(self) -> BaseLLMJudge:
        if self._judge is None:
            self._judge = LiteLLMJudge()
        return self._judge

    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> (
        tuple[float | None, str | None, float | None]
        | tuple[float | None, str | None, float | None, dict[str, Any] | None]
    ):
        rubric = (
            "You are an expert relevance evaluator. Evaluate whether the candidate "
            "response directly, comprehensively, and specifically answers the user's "
            "prompt without unnecessary tangents, evasion, or circularity. "
            "Score 1.0 for directly relevant and responsive; score 0.0 for "
            "completely irrelevant or evasive."
        )

        request = JudgeRequest(
            prompt=input_data.input,
            response=input_data.response,
            reference=input_data.expected_output,
            context=input_data.retrieved_context,
            rubric=rubric,
            metadata={
                "evaluator": self.name,
                "case_id": (str(input_data.case_id) if input_data.case_id else None),
            },
            model_info=input_data.model_info,
        )

        judge_resp = await self.judge.judge(request)
        metadata = {
            "evidence": judge_resp.evidence,
            "claims": judge_resp.claims,
            "judge_model": judge_resp.metadata.get("model", self.judge.model_name),
            "judge_metadata": judge_resp.metadata,
        }
        return (judge_resp.score, judge_resp.rationale, None, metadata)


class FaithfulnessEvaluator(AbstractEvaluator):
    """Context faithfulness (RAG) evaluator powered by an LLM Judge.

    Evaluates whether every claim in the response is strictly grounded in and
    inferable from the retrieved context passages.
    Returns status=UNAVAILABLE if retrieved context is not provided.
    """

    def __init__(
        self,
        name: str = "faithfulness",
        threshold: float | None = 0.8,
        judge: BaseLLMJudge | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=EvaluatorType.FAITHFULNESS,
            threshold=threshold,
            config=config,
        )
        self._judge = judge

    @property
    def judge(self) -> BaseLLMJudge:
        if self._judge is None:
            self._judge = LiteLLMJudge()
        return self._judge

    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> (
        tuple[float | None, str | None, float | None]
        | tuple[float | None, str | None, float | None, dict[str, Any] | None]
    ):
        if input_data.retrieved_context is None:
            return (
                None,
                "FaithfulnessEvaluator requires retrieved context passages, "
                "but none were provided.",
                None,
            )
        if (
            isinstance(input_data.retrieved_context, list)
            and len(input_data.retrieved_context) == 0
        ) or not input_data.retrieved_context:
            return (
                None,
                "FaithfulnessEvaluator requires non-empty retrieved context passages.",
                None,
            )

        rubric = (
            "You are an expert faithfulness evaluator. Evaluate whether every "
            "claim, fact, and statement in the candidate response is strictly "
            "grounded in and directly inferable from the provided context passages. "
            "Any unsupported claim must be penalized. Score 1.0 if the response "
            "is 100% faithful to the context; score 0.0 if the response contains "
            "claims unsupported or contradicted by the context."
        )

        request = JudgeRequest(
            prompt=input_data.input,
            response=input_data.response,
            reference=input_data.expected_output,
            context=input_data.retrieved_context,
            rubric=rubric,
            metadata={
                "evaluator": self.name,
                "case_id": (str(input_data.case_id) if input_data.case_id else None),
            },
            model_info=input_data.model_info,
        )

        judge_resp = await self.judge.judge(request)
        metadata = {
            "evidence": judge_resp.evidence,
            "claims": judge_resp.claims,
            "judge_model": judge_resp.metadata.get("model", self.judge.model_name),
            "judge_metadata": judge_resp.metadata,
        }
        return (judge_resp.score, judge_resp.rationale, None, metadata)


class HallucinationEvaluator(AbstractEvaluator):
    """Hallucination detection evaluator powered by an LLM Judge.

    Detects presence of fabricated or unsupported claims against reference
    ground truth or retrieved context passages.
    Returns status=UNAVAILABLE if neither expected_output nor context is provided.
    Score 1.0 indicates zero hallucinations (clean/truthful);
    Score 0.0 indicates severe hallucinations (fabricated).
    """

    def __init__(
        self,
        name: str = "hallucination",
        threshold: float | None = 0.8,
        judge: BaseLLMJudge | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=EvaluatorType.HALLUCINATION,
            threshold=threshold,
            config=config,
        )
        self._judge = judge

    @property
    def judge(self) -> BaseLLMJudge:
        if self._judge is None:
            self._judge = LiteLLMJudge()
        return self._judge

    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> (
        tuple[float | None, str | None, float | None]
        | tuple[float | None, str | None, float | None, dict[str, Any] | None]
    ):
        has_expected = bool(
            input_data.expected_output and input_data.expected_output.strip()
        )
        has_context = bool(input_data.retrieved_context)
        if not has_expected and not has_context:
            return (
                None,
                "HallucinationEvaluator requires expected_output or retrieved_context "
                "ground truth, but none was provided.",
                None,
            )

        rubric = (
            "You are an expert hallucination evaluator. Detect any fabricated, "
            "invented, or hallucinated claims in the candidate response that "
            "are unsupported by the reference ground truth or provided context. "
            "In this system, score 1.0 means NO hallucination detected "
            "(completely truthful and grounded), whereas score 0.0 means "
            "severe hallucination (fabricated or untruthful claims)."
        )

        request = JudgeRequest(
            prompt=input_data.input,
            response=input_data.response,
            reference=input_data.expected_output,
            context=input_data.retrieved_context,
            rubric=rubric,
            metadata={
                "evaluator": self.name,
                "case_id": (str(input_data.case_id) if input_data.case_id else None),
            },
            model_info=input_data.model_info,
        )

        judge_resp = await self.judge.judge(request)
        metadata = {
            "evidence": judge_resp.evidence,
            "claims": judge_resp.claims,
            "judge_model": judge_resp.metadata.get("model", self.judge.model_name),
            "judge_metadata": judge_resp.metadata,
        }
        return (judge_resp.score, judge_resp.rationale, None, metadata)


DEFAULT_CONSISTENCY_RUBRIC = (
    "Evaluate whether the candidate responses generated for the shared input "
    "are semantically consistent with each other. "
    "Check for agreement on core answers, presence of mutually contradictory claims, "
    "preservation of key facts, and material divergence in conclusions. "
    "Ignore stylistic, formatting, or lexical variations that do not alter "
    "substantive meaning."
)


class ConsistencyEvaluator(AbstractEvaluator):
    """Multi-response semantic consistency evaluator powered by an LLM Judge.

    Evaluates whether multiple candidate responses generated for the same input
    agree semantically on facts, assertions, and conclusions without contradiction.
    Requires at least 2 candidate responses.
    Returns status=UNAVAILABLE if fewer than 2 candidate responses are provided,
    or if any candidate response is empty or whitespace-only.
    """

    def __init__(
        self,
        name: str = "consistency",
        threshold: float | None = 0.8,
        judge: BaseLLMJudge | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=EvaluatorType.CONSISTENCY,
            threshold=threshold,
            config=config,
        )
        self._judge = judge

    @property
    def judge(self) -> BaseLLMJudge:
        if self._judge is None:
            self._judge = LiteLLMJudge()
        return self._judge

    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> (
        tuple[float | None, str | None, float | None]
        | tuple[float | None, str | None, float | None, dict[str, Any] | None]
    ):
        candidates = input_data.all_candidate_responses
        if candidates is None or len(candidates) < 2:
            count = len(candidates) if candidates else (1 if input_data.response else 0)
            return (
                None,
                (
                    "ConsistencyEvaluator requires at least 2 candidate responses "
                    f"for the same input, got {count}."
                ),
                None,
            )

        # Validate that candidates are non-empty strings
        for idx, c in enumerate(candidates, start=1):
            if not isinstance(c, str) or not c.strip():
                return (
                    None,
                    (
                        f"Candidate response #{idx} for ConsistencyEvaluator "
                        "cannot be empty or whitespace only."
                    ),
                    None,
                )

        rubric = (
            self.config.get("rubric") if self.config else None
        ) or DEFAULT_CONSISTENCY_RUBRIC

        request = JudgeRequest(
            prompt=input_data.input,
            response=candidates[0],
            candidate_responses=candidates,
            reference=input_data.expected_output,
            context=input_data.retrieved_context,
            rubric=rubric,
            metadata={
                "evaluator": self.name,
                "evaluator_type": self.evaluator_type.value,
                "candidate_count": len(candidates),
                "case_id": (str(input_data.case_id) if input_data.case_id else None),
            },
            model_info=input_data.model_info,
        )

        judge_resp = await self.judge.judge(request)
        metadata = {
            "evidence": judge_resp.evidence,
            "claims": judge_resp.claims,
            "judge_model": judge_resp.metadata.get("model", self.judge.model_name),
            "judge_metadata": judge_resp.metadata,
            "candidate_count": len(candidates),
        }
        return (judge_resp.score, judge_resp.rationale, None, metadata)
