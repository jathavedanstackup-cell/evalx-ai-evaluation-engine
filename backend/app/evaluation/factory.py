from typing import Any

from app.evaluation.adapters.deepeval import (
    DeepEvalFaithfulnessEvaluator,
    DeepEvalHallucinationEvaluator,
    DeepEvalRelevanceEvaluator,
)
from app.evaluation.contracts import BaseEvaluator
from app.evaluation.enums import EvaluatorBackend, EvaluatorType
from app.evaluation.errors import UnsupportedEvaluatorError
from app.evaluation.evaluators import (
    ConsistencyEvaluator,
    ExactMatchEvaluator,
    FactualityEvaluator,
    FaithfulnessEvaluator,
    HallucinationEvaluator,
    RelevanceEvaluator,
)


def create_evaluator(
    evaluator_type: EvaluatorType | str,
    backend: EvaluatorBackend | str = EvaluatorBackend.LLM_JUDGE,
    name: str | None = None,
    threshold: float | None = None,
    **kwargs: Any,
) -> BaseEvaluator:
    """Explicit factory for instantiating evaluators across backends.

    Supports:
      - EvaluatorBackend.NATIVE: deterministic exact match / instruction following.
      - EvaluatorBackend.LLM_JUDGE: native EVALX prompt-rubric judges
        (Factuality, Relevance, Faithfulness, Hallucination, Consistency).
      - EvaluatorBackend.DEEPEVAL: DeepEval-backed adapters
        (Relevance, Faithfulness, Hallucination).
    """
    try:
        norm_type = (
            evaluator_type
            if isinstance(evaluator_type, EvaluatorType)
            else EvaluatorType(str(evaluator_type).lower())
        )
    except ValueError as err:
        raise UnsupportedEvaluatorError(
            f"Unsupported evaluator type '{evaluator_type}'.",
            details={"supported_types": [t.value for t in EvaluatorType]},
        ) from err

    try:
        norm_backend = (
            backend
            if isinstance(backend, EvaluatorBackend)
            else EvaluatorBackend(str(backend).lower())
        )
    except ValueError as err:
        raise UnsupportedEvaluatorError(
            f"Unsupported evaluator backend '{backend}'.",
            details={"supported_backends": [b.value for b in EvaluatorBackend]},
        ) from err

    if norm_backend == EvaluatorBackend.DEEPEVAL:
        if norm_type == EvaluatorType.RELEVANCE:
            return DeepEvalRelevanceEvaluator(
                name=name or "deepeval_relevance",
                threshold=threshold,
                **kwargs,
            )
        if norm_type == EvaluatorType.FAITHFULNESS:
            return DeepEvalFaithfulnessEvaluator(
                name=name or "deepeval_faithfulness",
                threshold=threshold,
                **kwargs,
            )
        if norm_type == EvaluatorType.HALLUCINATION:
            return DeepEvalHallucinationEvaluator(
                name=name or "deepeval_hallucination",
                threshold=threshold,
                **kwargs,
            )
        raise UnsupportedEvaluatorError(
            f"DeepEval backend does not support evaluator type '{norm_type.value}'.",
            evaluator_type=norm_type,
            details={
                "supported_deepeval_types": [
                    EvaluatorType.RELEVANCE.value,
                    EvaluatorType.FAITHFULNESS.value,
                    EvaluatorType.HALLUCINATION.value,
                ]
            },
        )

    if norm_backend == EvaluatorBackend.LLM_JUDGE:
        if norm_type == EvaluatorType.FACTUALITY:
            return FactualityEvaluator(
                name=name or "factuality",
                threshold=threshold if threshold is not None else 0.8,
                **kwargs,
            )
        if norm_type == EvaluatorType.RELEVANCE:
            return RelevanceEvaluator(
                name=name or "relevance",
                threshold=threshold if threshold is not None else 0.8,
                **kwargs,
            )
        if norm_type == EvaluatorType.FAITHFULNESS:
            return FaithfulnessEvaluator(
                name=name or "faithfulness",
                threshold=threshold if threshold is not None else 0.8,
                **kwargs,
            )
        if norm_type == EvaluatorType.HALLUCINATION:
            return HallucinationEvaluator(
                name=name or "hallucination",
                threshold=threshold if threshold is not None else 0.8,
                **kwargs,
            )
        if norm_type == EvaluatorType.CONSISTENCY:
            return ConsistencyEvaluator(
                name=name or "consistency",
                threshold=threshold if threshold is not None else 0.8,
                **kwargs,
            )
        raise UnsupportedEvaluatorError(
            f"LLM-judge backend does not support evaluator type '{norm_type.value}'.",
            evaluator_type=norm_type,
        )

    if norm_backend == EvaluatorBackend.NATIVE:
        if norm_type == EvaluatorType.INSTRUCTION_FOLLOWING:
            return ExactMatchEvaluator(
                name=name or "exact_match",
                threshold=threshold if threshold is not None else 1.0,
                **kwargs,
            )
        raise UnsupportedEvaluatorError(
            f"Native backend does not support evaluator type '{norm_type.value}' "
            "directly via factory. Instantiate deterministic evaluator classes "
            "directly.",
            evaluator_type=norm_type,
        )

    raise UnsupportedEvaluatorError(
        f"Unhandled backend configuration '{norm_backend}'."
    )
