import re
from typing import Any
from uuid import UUID

from app.evaluation.enums import EvaluatorType

_SENSITIVE_EXACT_KEYS = {
    "auth",
    "authorization",
    "bearer",
    "key",
    "password",
    "secret",
    "token",
}
_SENSITIVE_SUBSTRINGS = (
    "api_key",
    "apikey",
    "access_token",
    "auth_token",
    "bearer_token",
    "client_secret",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret_key",
)


def is_sensitive_key(key: str) -> bool:
    """Return True if the key represents a sensitive credential, token, or secret."""
    k_lower = str(key).lower().strip()
    if k_lower in _SENSITIVE_EXACT_KEYS:
        return True
    if any(sub in k_lower for sub in _SENSITIVE_SUBSTRINGS):
        return True
    return k_lower.endswith(("_key", "_token", "_secret", "_pwd"))


_URL_USERINFO_RE = re.compile(r"://([^@/]+)@")


def sanitize_url_credentials(text: str) -> str:
    """Masks embedded credentials in connection URLs (e.g. redis://user:pass@host -> redis://[REDACTED]@host)."""
    return _URL_USERINFO_RE.sub("://[REDACTED]@", text)


def sanitize_error_message(msg: str) -> str:
    """Removes sensitive keys, secrets, and URL credentials from error text."""
    if not msg:
        return ""
    cleaned = sanitize_url_credentials(msg)
    words = cleaned.split()
    sanitized: list[str] = []
    for w in words:
        token = w.split("=")[0].split(":")[0]
        if is_sensitive_key(w) or is_sensitive_key(token):
            sanitized.append("[REDACTED]")
        else:
            sanitized.append(w)
    return " ".join(sanitized)


def sanitize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Recursively redact credentials, tokens, and secrets from error details."""
    if not details:
        return {}

    sanitized: dict[str, Any] = {}
    for k, v in details.items():
        if is_sensitive_key(str(k)):
            sanitized[k] = "[REDACTED]"
        elif isinstance(v, dict):
            sanitized[k] = sanitize_details(v)
        elif isinstance(v, list):
            sanitized[k] = [
                sanitize_details(item) if isinstance(item, dict) else item for item in v
            ]
        else:
            sanitized[k] = v
    return sanitized


class EvaluationError(Exception):
    """Base exception for all EVALX evaluation engine errors.

    Guarantees structured attributes and safe string representations that
    never expose internal credentials or provider secrets.
    """

    def __init__(
        self,
        message: str,
        evaluator_type: EvaluatorType | str | None = None,
        case_id: UUID | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        sanitized_msg = sanitize_error_message(message)
        super().__init__(sanitized_msg)
        self.message = sanitized_msg
        self.evaluator_type = (
            EvaluatorType(evaluator_type)
            if isinstance(evaluator_type, str)
            and evaluator_type in EvaluatorType._value2member_map_
            else evaluator_type
        )
        self.case_id = case_id
        self.details = sanitize_details(details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "evaluator_type": (
                self.evaluator_type.value
                if isinstance(self.evaluator_type, EvaluatorType)
                else self.evaluator_type
            ),
            "case_id": str(self.case_id) if self.case_id else None,
            "details": self.details,
        }

    def __str__(self) -> str:
        parts = [f"{self.__class__.__name__}: {self.message}"]
        if self.evaluator_type:
            parts.append(f"evaluator={self.evaluator_type}")
        if self.case_id:
            parts.append(f"case_id={self.case_id}")
        return " | ".join(parts)


class InvalidEvaluationInputError(EvaluationError):
    """Raised when an EvaluationInput fails validation or is missing fields."""


class UnsupportedEvaluatorError(EvaluationError):
    """Raised when an evaluator is not recognized or supported."""


class EvaluatorExecutionError(EvaluationError):
    """Raised when an evaluator encounters an unrecoverable runtime failure."""


class ModelResponseUnavailableError(EvaluationError):
    """Raised when the AI model response to be evaluated is missing or empty."""


class MalformedEvaluatorOutputError(EvaluationError):
    """Raised when an evaluator output violates the EvaluationMetric contract."""


class EvaluationTimeoutError(EvaluationError):
    """Raised when an evaluator exceeds its execution timeout budget."""


class UnavailableDependencyError(EvaluationError):
    """Raised when a required library or service for an evaluator is missing."""


class InvalidRunStateTransitionError(EvaluationError):
    """Raised when an EvaluationRun attempts an illegal lifecycle transition."""


class RunEnqueueError(EvaluationError):
    """Raised when an evaluation run fails to be submitted to the background queue."""


class QueueUnavailableError(RunEnqueueError):
    """Raised when the queue infrastructure (e.g. Redis) is unavailable."""
