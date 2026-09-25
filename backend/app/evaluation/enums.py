from enum import StrEnum


class EvaluatorType(StrEnum):
    """Initial core evaluator categories for EVALX."""

    FACTUALITY = "factuality"
    RELEVANCE = "relevance"
    FAITHFULNESS = "faithfulness"
    INSTRUCTION_FOLLOWING = "instruction_following"
    CONSISTENCY = "consistency"
    HALLUCINATION = "hallucination"


class MetricStatus(StrEnum):
    """Execution status of an individual metric evaluation.

    Allows distinguishing failed thresholds from execution errors or missing metrics.
    """

    SUCCESS = "success"
    THRESHOLD_FAILED = "threshold_failed"
    ERROR = "error"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


class ScoreDirection(StrEnum):
    """Documents the quality directionality of an evaluation metric score.

    In EVALX standard scoring, all metric scores are normalized to [0.0, 1.0]
    where 1.0 indicates optimal quality / best possible outcome.
    """

    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


class EvaluatorBackend(StrEnum):
    """Backend implementation providing the evaluation capability."""

    NATIVE = "native"
    LLM_JUDGE = "llm_judge"
    DEEPEVAL = "deepeval"
