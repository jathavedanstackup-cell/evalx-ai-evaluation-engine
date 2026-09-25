from app.evaluation.adapters import (
    DeepEvalBaseAdapterEvaluator,
    DeepEvalFaithfulnessEvaluator,
    DeepEvalHallucinationEvaluator,
    DeepEvalRelevanceEvaluator,
)
from app.evaluation.contracts import AbstractEvaluator, BaseEvaluator
from app.evaluation.engine import EvaluationEngine
from app.evaluation.enums import (
    EvaluatorBackend,
    EvaluatorType,
    MetricStatus,
    ScoreDirection,
)
from app.evaluation.errors import (
    EvaluationError,
    EvaluationTimeoutError,
    EvaluatorExecutionError,
    InvalidEvaluationInputError,
    MalformedEvaluatorOutputError,
    ModelResponseUnavailableError,
    UnavailableDependencyError,
    UnsupportedEvaluatorError,
)
from app.evaluation.evaluators import (
    ConsistencyEvaluator,
    ContainsEvaluator,
    ExactMatchEvaluator,
    FactualityEvaluator,
    FaithfulnessEvaluator,
    HallucinationEvaluator,
    JSONSchemaEvaluator,
    KeywordConstraintEvaluator,
    LengthConstraintEvaluator,
    OutputFormatEvaluator,
    RelevanceEvaluator,
)
from app.evaluation.factory import create_evaluator
from app.evaluation.judges import (
    BaseLLMJudge,
    JudgeRequest,
    JudgeResponse,
    LiteLLMJudge,
)
from app.evaluation.registry import EvaluatorRegistry
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
    EvaluationRunResult,
    ModelInfo,
)

__all__ = [
    # Contracts
    "AbstractEvaluator",
    "BaseEvaluator",
    "EvaluationEngine",
    "EvaluatorRegistry",
    # Factory
    "create_evaluator",
    # Judges
    "BaseLLMJudge",
    "JudgeRequest",
    "JudgeResponse",
    "LiteLLMJudge",
    # Evaluators
    "ConsistencyEvaluator",
    "ContainsEvaluator",
    "ExactMatchEvaluator",
    "FactualityEvaluator",
    "FaithfulnessEvaluator",
    "HallucinationEvaluator",
    "JSONSchemaEvaluator",
    "KeywordConstraintEvaluator",
    "LengthConstraintEvaluator",
    "OutputFormatEvaluator",
    "RelevanceEvaluator",
    # DeepEval Adapters
    "DeepEvalBaseAdapterEvaluator",
    "DeepEvalFaithfulnessEvaluator",
    "DeepEvalHallucinationEvaluator",
    "DeepEvalRelevanceEvaluator",
    # Enums
    "EvaluatorBackend",
    "EvaluatorType",
    "MetricStatus",
    "ScoreDirection",
    # Errors
    "EvaluationError",
    "EvaluationTimeoutError",
    "EvaluatorExecutionError",
    "InvalidEvaluationInputError",
    "MalformedEvaluatorOutputError",
    "ModelResponseUnavailableError",
    "UnavailableDependencyError",
    "UnsupportedEvaluatorError",
    # Types
    "EvaluationCaseResult",
    "EvaluationContext",
    "EvaluationInput",
    "EvaluationMetric",
    "EvaluationRunResult",
    "ModelInfo",
    # Scoring
    "calculate_case_score",
    "calculate_run_score",
    "evaluate_metric_threshold",
    "validate_score_range",
]
