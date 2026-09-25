from collections.abc import Iterator, Sequence

from app.evaluation.contracts import BaseEvaluator
from app.evaluation.enums import EvaluatorType
from app.evaluation.errors import UnsupportedEvaluatorError


class EvaluatorRegistry:
    """Registry managing available evaluator instances and type lookups.

    Instantiable per-engine or per-test; avoids global singleton abuse.
    """

    def __init__(self) -> None:
        self._evaluators: dict[str, BaseEvaluator] = {}
        self._type_index: dict[EvaluatorType, list[str]] = {
            t: [] for t in EvaluatorType
        }

    def register(self, evaluator: BaseEvaluator) -> None:
        """Register an evaluator instance.

        Overwrites any previous evaluator with the same name.
        """
        name = evaluator.name
        if name in self._evaluators:
            old_eval = self._evaluators[name]
            if name in self._type_index.get(old_eval.evaluator_type, []):
                self._type_index[old_eval.evaluator_type].remove(name)

        self._evaluators[name] = evaluator
        self._type_index.setdefault(evaluator.evaluator_type, []).append(name)

    def unregister(self, name: str) -> None:
        """Unregister an evaluator by name."""
        if name in self._evaluators:
            evaluator = self._evaluators.pop(name)
            if name in self._type_index.get(evaluator.evaluator_type, []):
                self._type_index[evaluator.evaluator_type].remove(name)

    def get(self, name: str) -> BaseEvaluator:
        """Retrieve an evaluator by name.

        Raises UnsupportedEvaluatorError if not found.
        """
        if name not in self._evaluators:
            raise UnsupportedEvaluatorError(
                f"Evaluator '{name}' is not registered.",
                details={"available_evaluators": list(self._evaluators.keys())},
            )
        return self._evaluators[name]

    def has(self, name: str) -> bool:
        """Check if an evaluator is registered by name."""
        return name in self._evaluators

    def get_by_type(self, evaluator_type: EvaluatorType | str) -> list[BaseEvaluator]:
        """Retrieve all registered evaluators matching a specific EvaluatorType.

        Raises UnsupportedEvaluatorError if the type is invalid.
        """
        norm_type: EvaluatorType
        if isinstance(evaluator_type, EvaluatorType):
            norm_type = evaluator_type
        else:
            try:
                norm_type = EvaluatorType(evaluator_type)
            except ValueError as err:
                raise UnsupportedEvaluatorError(
                    f"Unsupported evaluator type '{evaluator_type}'.",
                    details={"supported_types": [t.value for t in EvaluatorType]},
                ) from err

        names = self._type_index.get(norm_type, [])
        return [self._evaluators[n] for n in names if n in self._evaluators]

    def list_evaluators(self) -> list[str]:
        """Return the names of all registered evaluators."""
        return list(self._evaluators.keys())

    def list_by_type(self, evaluator_type: EvaluatorType | str) -> list[str]:
        """Return evaluator names registered under the given EvaluatorType."""
        evaluators = self.get_by_type(evaluator_type)
        return [e.name for e in evaluators]

    def register_all(self, evaluators: Sequence[BaseEvaluator]) -> None:
        """Register multiple evaluator instances at once."""
        for e in evaluators:
            self.register(e)

    def values(self) -> list[BaseEvaluator]:
        """Return all registered evaluator instances."""
        return list(self._evaluators.values())

    def __iter__(self) -> Iterator[BaseEvaluator]:
        return iter(self._evaluators.values())

    def __len__(self) -> int:
        return len(self._evaluators)
