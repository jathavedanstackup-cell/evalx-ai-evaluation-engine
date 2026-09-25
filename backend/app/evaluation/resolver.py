from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.contracts import BaseEvaluator
from app.evaluation.enums import EvaluatorBackend
from app.evaluation.errors import UnsupportedEvaluatorError
from app.evaluation.factory import create_evaluator
from app.schemas.evaluation_config import EvaluatorDefinitionInput

if TYPE_CHECKING:
    from app.models.evaluation_configuration import (
        EvaluationConfig,
        EvaluationConfigVersion,
    )


class ResolvedConfiguration:
    """Encapsulates instantiated evaluators and their corresponding relative weights."""

    def __init__(
        self,
        evaluators: list[BaseEvaluator],
        weights: dict[str, float],
    ) -> None:
        self.evaluators = evaluators
        self.weights = weights


class ConfigurationResolver:
    """Dedicated resolver bridging evaluation configurations to instantiated
    evaluators and weights.

    Responsibilities:
      - Validates requested evaluator definitions
      - Instantiates evaluator implementations via canonical create_evaluator factory
      - Applies thresholds and weights
      - Excludes disabled evaluators
      - Rejects unsupported combinations deterministically
    """

    @staticmethod
    def resolve_evaluators(
        definitions: Sequence[EvaluatorDefinitionInput | dict[str, Any]],
    ) -> ResolvedConfiguration:
        instantiated: list[BaseEvaluator] = []
        weights_map: dict[str, float] = {}

        for defn in definitions:
            if isinstance(defn, dict):
                e_type = str(defn.get("evaluator_type", ""))
                e_backend = str(defn.get("backend", EvaluatorBackend.LLM_JUDGE.value))
                e_threshold = defn.get("threshold")
                e_weight = float(defn.get("weight", 1.0))
                e_enabled = bool(defn.get("enabled", True))
                e_name = defn.get("name")
                e_cfg = dict(defn.get("configuration") or {})
            else:
                e_type = defn.evaluator_type
                e_backend = defn.backend
                e_threshold = defn.threshold
                e_weight = float(defn.weight)
                e_enabled = bool(defn.enabled)
                e_name = defn.name
                e_cfg = dict(defn.configuration or {})

            if not e_enabled:
                continue

            effective_name = e_name or e_type
            kwargs = dict(e_cfg)
            kwargs.setdefault("config", e_cfg)

            evaluator = create_evaluator(
                evaluator_type=e_type,
                backend=e_backend,
                name=effective_name,
                threshold=e_threshold,
                **kwargs,
            )
            instantiated.append(evaluator)
            weights_map[evaluator.name] = e_weight

        if not instantiated:
            raise UnsupportedEvaluatorError(
                "Configuration does not have any enabled evaluators to execute."
            )

        return ResolvedConfiguration(evaluators=instantiated, weights=weights_map)

    @staticmethod
    async def resolve_configuration(
        session: AsyncSession,
        config_id: UUID,
        owner_user_id: UUID | None,
        version: int | None = None,
    ) -> EvaluationConfig | EvaluationConfigVersion | None:
        from app.services import evaluation_config_service

        return await evaluation_config_service.get_configuration(
            session=session,
            config_id=config_id,
            owner_user_id=owner_user_id,
            version=version,
        )
