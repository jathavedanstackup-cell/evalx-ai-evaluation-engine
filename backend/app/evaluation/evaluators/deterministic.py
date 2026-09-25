import json
import re
import string
from typing import Any

import jsonschema  # type: ignore[import-untyped]

from app.evaluation.contracts import AbstractEvaluator
from app.evaluation.enums import EvaluatorType
from app.evaluation.types import EvaluationInput


class ExactMatchEvaluator(AbstractEvaluator):
    """Normalized exact comparison between model response and expected output.

    Scores 1.0 on match, 0.0 on mismatch.
    Returns status=UNAVAILABLE if expected_output is not provided.
    """

    def __init__(
        self,
        name: str = "exact_match",
        threshold: float | None = 1.0,
        case_sensitive: bool = False,
        strip_whitespace: bool = True,
        ignore_punctuation: bool = False,
        evaluator_type: EvaluatorType = EvaluatorType.INSTRUCTION_FOLLOWING,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=evaluator_type,
            threshold=threshold,
            config=config,
        )
        self.case_sensitive = case_sensitive
        self.strip_whitespace = strip_whitespace
        self.ignore_punctuation = ignore_punctuation

    def _normalize(self, text: str) -> str:
        s = text
        if self.strip_whitespace:
            s = " ".join(s.split())
        if not self.case_sensitive:
            s = s.lower()
        if self.ignore_punctuation:
            s = s.translate(str.maketrans("", "", string.punctuation))
            if self.strip_whitespace:
                s = " ".join(s.split())
        return s

    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> tuple[float | None, str | None, float | None]:
        if not input_data.expected_output:
            return (
                None,
                "ExactMatchEvaluator requires expected_output, but none was provided.",
                None,
            )

        norm_resp = self._normalize(input_data.response)
        norm_exp = self._normalize(input_data.expected_output)

        is_match = norm_resp == norm_exp
        score = 1.0 if is_match else 0.0
        if is_match:
            explanation = "Response exactly matches expected output."
        else:
            explanation = (
                f"Response did not match expected output "
                f"('{norm_resp}' != '{norm_exp}')."
            )
        return score, explanation, 1.0


class ContainsEvaluator(AbstractEvaluator):
    """Evaluates whether the response contains required substrings or expected output.

    Scores 1.0 if required substrings are present, 0.0 otherwise.
    """

    def __init__(
        self,
        name: str = "contains",
        threshold: float | None = 1.0,
        substrings: list[str] | None = None,
        case_sensitive: bool = False,
        require_all: bool = True,
        evaluator_type: EvaluatorType = EvaluatorType.INSTRUCTION_FOLLOWING,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=evaluator_type,
            threshold=threshold,
            config=config,
        )
        self.substrings = substrings
        self.case_sensitive = case_sensitive
        self.require_all = require_all

    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> tuple[float | None, str | None, float | None]:
        targets = self.substrings
        if not targets:
            if input_data.expected_output:
                targets = [input_data.expected_output]
            else:
                return (
                    None,
                    "ContainsEvaluator requires either configured substrings "
                    "or expected_output.",
                    None,
                )

        target_list = [t for t in targets if t.strip()]
        if not target_list:
            return (
                None,
                "ContainsEvaluator targets cannot be empty or whitespace only.",
                None,
            )

        resp = (
            input_data.response if self.case_sensitive else input_data.response.lower()
        )

        matched: list[str] = []
        missing: list[str] = []
        for target in target_list:
            t_check = target if self.case_sensitive else target.lower()
            if t_check in resp:
                matched.append(target)
            else:
                missing.append(target)

        if self.require_all:
            score = 1.0 if len(missing) == 0 else 0.0
        else:
            score = round(len(matched) / len(target_list), 6)

        if score == 1.0:
            explanation = (
                f"All {len(target_list)} required substring(s) found in response."
            )
        else:
            explanation = (
                f"Missing {len(missing)} of {len(target_list)} required substring(s): "
                f"{missing}."
            )

        return score, explanation, 1.0


class KeywordConstraintEvaluator(AbstractEvaluator):
    """Validates presence of required keywords and absence of forbidden keywords.

    Scores 0.0 immediately if any forbidden keyword is found.
    Scores proportionally on presence of required keywords (1.0 if all present).
    """

    def __init__(
        self,
        name: str = "keyword_constraint",
        threshold: float | None = 1.0,
        required_keywords: list[str] | None = None,
        forbidden_keywords: list[str] | None = None,
        case_sensitive: bool = False,
        word_boundary: bool = True,
        evaluator_type: EvaluatorType = EvaluatorType.INSTRUCTION_FOLLOWING,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=evaluator_type,
            threshold=threshold,
            config=config,
        )
        self.required_keywords = [
            k.strip() for k in (required_keywords or []) if k.strip()
        ]
        self.forbidden_keywords = [
            k.strip() for k in (forbidden_keywords or []) if k.strip()
        ]
        self.case_sensitive = case_sensitive
        self.word_boundary = word_boundary

        if not self.required_keywords and not self.forbidden_keywords:
            raise ValueError(
                "KeywordConstraintEvaluator requires at least one required or "
                "forbidden keyword."
            )

    def _contains_keyword(self, text: str, keyword: str) -> bool:
        flags = 0 if self.case_sensitive else re.IGNORECASE
        if self.word_boundary:
            pattern = rf"\b{re.escape(keyword)}\b"
        else:
            pattern = re.escape(keyword)
        return bool(re.search(pattern, text, flags=flags))

    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> tuple[float | None, str | None, float | None]:
        resp = input_data.response

        # 1. Check forbidden keywords first (hard failure)
        found_forbidden: list[str] = [
            kw for kw in self.forbidden_keywords if self._contains_keyword(resp, kw)
        ]
        if found_forbidden:
            return (
                0.0,
                f"Forbidden keyword(s) detected in response: {found_forbidden}.",
                1.0,
            )

        # 2. Check required keywords
        if not self.required_keywords:
            return (
                1.0,
                "No forbidden keywords detected; no required keywords configured.",
                1.0,
            )

        found_required: list[str] = [
            kw for kw in self.required_keywords if self._contains_keyword(resp, kw)
        ]
        missing_required = [
            kw for kw in self.required_keywords if kw not in found_required
        ]

        score = round(len(found_required) / len(self.required_keywords), 6)
        if score == 1.0:
            explanation = (
                f"All {len(self.required_keywords)} required keywords present."
            )
        else:
            explanation = (
                f"Found {len(found_required)}/{len(self.required_keywords)} "
                f"required keywords. Missing: {missing_required}."
            )

        return score, explanation, 1.0


class LengthConstraintEvaluator(AbstractEvaluator):
    """Evaluates response length against min and max bounds in characters or words.

    Scores 1.0 if within bounds, 0.0 otherwise.
    """

    def __init__(
        self,
        name: str = "length_constraint",
        threshold: float | None = 1.0,
        min_length: int | None = None,
        max_length: int | None = None,
        unit: str = "character",
        evaluator_type: EvaluatorType = EvaluatorType.INSTRUCTION_FOLLOWING,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=evaluator_type,
            threshold=threshold,
            config=config,
        )
        if min_length is None and max_length is None:
            raise ValueError(
                "LengthConstraintEvaluator requires min_length, max_length, or both."
            )
        if min_length is not None and min_length < 0:
            raise ValueError(f"min_length must be non-negative, got {min_length}")
        if max_length is not None and max_length < 0:
            raise ValueError(f"max_length must be non-negative, got {max_length}")
        if (
            min_length is not None
            and max_length is not None
            and min_length > max_length
        ):
            raise ValueError(
                f"min_length ({min_length}) cannot exceed max_length ({max_length})."
            )
        if unit not in ("character", "word"):
            raise ValueError(
                f"Invalid unit '{unit}'. Supported units: 'character', 'word'."
            )

        self.min_length = min_length
        self.max_length = max_length
        self.unit = unit

    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> tuple[float | None, str | None, float | None]:
        text = input_data.response
        count = len(text) if self.unit == "character" else len(text.split())

        min_ok = self.min_length is None or count >= self.min_length
        max_ok = self.max_length is None or count <= self.max_length

        if min_ok and max_ok:
            return (
                1.0,
                f"Length {count} {self.unit}s satisfies constraints "
                f"[min={self.min_length or 0}, max={self.max_length or 'inf'}].",
                1.0,
            )

        reasons: list[str] = []
        if not min_ok:
            reasons.append(f"below minimum ({count} < {self.min_length} {self.unit}s)")
        if not max_ok:
            reasons.append(
                f"exceeds maximum ({count} > {self.max_length} {self.unit}s)"
            )

        return (
            0.0,
            f"Length constraint violated: {', '.join(reasons)}.",
            1.0,
        )


class JSONSchemaEvaluator(AbstractEvaluator):
    """Validates structured model response against a specified JSON schema.

    Scores 1.0 if valid and conforms to schema, 0.0 otherwise.
    Safe error reporting without leaking internal system traces.
    """

    def __init__(
        self,
        schema: dict[str, Any],
        name: str = "json_schema",
        threshold: float | None = 1.0,
        evaluator_type: EvaluatorType = EvaluatorType.INSTRUCTION_FOLLOWING,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=evaluator_type,
            threshold=threshold,
            config=config,
        )
        if not isinstance(schema, dict) or not schema:
            raise ValueError(
                "JSONSchemaEvaluator requires a non-empty schema dictionary."
            )
        self.schema = schema
        # Validate that the provided schema itself is a valid JSON schema
        try:
            jsonschema.Draft202012Validator.check_schema(self.schema)
        except jsonschema.SchemaError as err:
            raise ValueError(f"Invalid JSON schema definition: {err.message}") from err

    def _extract_json_string(self, text: str) -> str:
        s = text.strip()
        # Handle markdown code fence wrapping if present
        if s.startswith("```"):
            lines = s.splitlines()
            if (
                len(lines) >= 2
                and lines[0].startswith("```")
                and lines[-1].strip() == "```"
            ):
                return "\n".join(lines[1:-1]).strip()
        return s

    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> tuple[float | None, str | None, float | None]:
        raw_text = self._extract_json_string(input_data.response)

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as err:
            return (
                0.0,
                f"Response is not valid JSON: {err.msg} "
                f"(at line {err.lineno}, col {err.colno}).",
                1.0,
            )

        validator = jsonschema.Draft202012Validator(self.schema)
        errors = list(validator.iter_errors(parsed))

        if not errors:
            return 1.0, "JSON response conforms to specified schema.", 1.0

        error_summaries = [f"Path '{e.json_path}': {e.message}" for e in errors[:5]]
        explanation = (
            f"JSON schema validation failed ({len(errors)} error(s)): "
            f"{'; '.join(error_summaries)}."
        )
        return 0.0, explanation, 1.0


class OutputFormatEvaluator(AbstractEvaluator):
    """Verifies that the response conforms to an explicit format.

    Supported formats: 'json', 'plain_text', 'list', 'markdown'.
    Does NOT infer format from vague heuristics.
    """

    SUPPORTED_FORMATS = ("json", "plain_text", "list", "markdown")

    def __init__(
        self,
        expected_format: str,
        name: str = "output_format",
        threshold: float | None = 1.0,
        evaluator_type: EvaluatorType = EvaluatorType.INSTRUCTION_FOLLOWING,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            evaluator_type=evaluator_type,
            threshold=threshold,
            config=config,
        )
        norm_fmt = expected_format.strip().lower()
        if norm_fmt not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported format '{expected_format}'. "
                f"Supported formats: {list(self.SUPPORTED_FORMATS)}."
            )
        self.expected_format = norm_fmt

    def _is_json(self, text: str) -> bool:
        s = text.strip()
        if s.startswith("```"):
            lines = s.splitlines()
            if (
                len(lines) >= 2
                and lines[0].startswith("```")
                and lines[-1].strip() == "```"
            ):
                s = "\n".join(lines[1:-1]).strip()
        try:
            json.loads(s)
            return True
        except ValueError, TypeError:
            return False

    def _is_list(self, text: str) -> bool:
        # Check if valid JSON array
        s = text.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return True
            except ValueError, TypeError:
                pass

        # Check line-based list markers (at least 2 items required)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return False

        bullet_pattern = re.compile(r"^[-*•]\s+.+")
        numbered_pattern = re.compile(r"^\d+[\.\)]\s+.+")

        bullet_count = sum(1 for line in lines if bullet_pattern.match(line))
        numbered_count = sum(1 for line in lines if numbered_pattern.match(line))

        return bullet_count >= 2 or numbered_count >= 2

    def _is_markdown(self, text: str) -> bool:
        # Require explicit markdown syntax constructs
        md_patterns = [
            r"^#{1,6}\s+.+",  # Headers
            r"\*\*[^*]+\*\*",  # Bold
            r"__[^_]+__",  # Underline/bold
            r"^[-*•]\s+.+",  # Bullet points
            r"^\d+\.\s+.+",  # Numbered lists
            r"```[\s\S]*?```",  # Fenced code block
            r"`[^`]+`",  # Inline code
            r"\[.+?\]\(.+?\)",  # Links
            r"\|.+\|.+\|",  # Tables
        ]
        combined = re.compile("|".join(f"(?:{p})" for p in md_patterns), re.MULTILINE)
        return bool(combined.search(text))

    def _is_plain_text(self, text: str) -> bool:
        s = text.strip()
        if not s:
            return False
        # Plain text must not be raw JSON or raw fenced code block
        if s.startswith("{") and s.endswith("}") and self._is_json(s):
            return False
        if s.startswith("```") and s.endswith("```"):
            return False
        # Must not contain unrendered HTML tags
        if bool(re.search(r"<[a-zA-Z][^>]*>", text)):
            return False
        return True

    async def _compute_score(
        self, input_data: EvaluationInput
    ) -> tuple[float | None, str | None, float | None]:
        text = input_data.response

        if self.expected_format == "json":
            is_valid = self._is_json(text)
        elif self.expected_format == "list":
            is_valid = self._is_list(text)
        elif self.expected_format == "markdown":
            is_valid = self._is_markdown(text)
        elif self.expected_format == "plain_text":
            is_valid = self._is_plain_text(text)
        else:
            is_valid = False

        score = 1.0 if is_valid else 0.0
        if is_valid:
            explanation = (
                f"Response conforms to expected '{self.expected_format}' format."
            )
        else:
            explanation = (
                f"Response does not conform to expected "
                f"'{self.expected_format}' format."
            )

        return score, explanation, 1.0
