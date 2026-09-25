"""Typed exception hierarchy for the EVALX Python SDK."""

from __future__ import annotations

from typing import Any


class EvalXError(Exception):
    """Base exception for all EVALX client and API errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        correlation_id: str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.correlation_id = correlation_id
        self.response_body = response_body

    def __repr__(self) -> str:
        parts = [f"message={self.message!r}"]
        if self.status_code is not None:
            parts.append(f"status_code={self.status_code}")
        if self.error_code:
            parts.append(f"error_code={self.error_code!r}")
        if self.correlation_id:
            parts.append(f"correlation_id={self.correlation_id!r}")
        return f"{self.__class__.__name__}({', '.join(parts)})"


class EvalXConnectionError(EvalXError):
    """Raised when the SDK cannot establish a network connection to the server."""


class EvalXTimeoutError(EvalXError):
    """Raised when a request or polling operation times out."""


class EvalXValidationError(EvalXError):
    """Raised when request payload fails validation (HTTP 422)."""

    def __init__(
        self,
        message: str,
        *,
        errors: list[dict[str, Any]] | None = None,
        status_code: int = 422,
        error_code: str | None = "VALIDATION_ERROR",
        correlation_id: str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            correlation_id=correlation_id,
            response_body=response_body,
        )
        self.errors = errors or []


class EvalXAuthenticationError(EvalXError):
    """Raised when request credentials are missing or invalid (HTTP 401)."""

    def __init__(
        self,
        message: str = "Missing or invalid authentication credentials.",
        *,
        status_code: int = 401,
        error_code: str | None = "UNAUTHORIZED",
        correlation_id: str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            correlation_id=correlation_id,
            response_body=response_body,
        )


class EvalXAuthorizationError(EvalXError):
    """Raised when tenant lacks permission for the requested resource (HTTP 403)."""

    def __init__(
        self,
        message: str = "Forbidden: insufficient permissions for resource.",
        *,
        status_code: int = 403,
        error_code: str | None = "FORBIDDEN",
        correlation_id: str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            correlation_id=correlation_id,
            response_body=response_body,
        )


class EvalXNotFoundError(EvalXError):
    """Raised when the requested resource does not exist (HTTP 404)."""

    def __init__(
        self,
        message: str = "Requested resource not found.",
        *,
        status_code: int = 404,
        error_code: str | None = "NOT_FOUND",
        correlation_id: str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            correlation_id=correlation_id,
            response_body=response_body,
        )


class EvalXConflictError(EvalXError):
    """Raised on state conflict or duplicate unique constraint (HTTP 409)."""

    def __init__(
        self,
        message: str = "Resource conflict detected.",
        *,
        status_code: int = 409,
        error_code: str | None = "CONFLICT",
        correlation_id: str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            correlation_id=correlation_id,
            response_body=response_body,
        )


class EvalXPayloadTooLargeError(EvalXError):
    """Raised when request payload exceeds server body limits (HTTP 413)."""

    def __init__(
        self,
        message: str = "Request payload exceeds maximum allowable size limit.",
        *,
        status_code: int = 413,
        error_code: str | None = "PAYLOAD_TOO_LARGE",
        correlation_id: str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            correlation_id=correlation_id,
            response_body=response_body,
        )


class EvalXRateLimitError(EvalXError):
    """Raised when client exceeds API rate limits (HTTP 429)."""

    def __init__(
        self,
        message: str = "Rate limit exceeded. Please back off and retry.",
        *,
        retry_after: float | None = None,
        status_code: int = 429,
        error_code: str | None = "RATE_LIMIT_EXCEEDED",
        correlation_id: str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            correlation_id=correlation_id,
            response_body=response_body,
        )
        self.retry_after = retry_after


class EvalXServerError(EvalXError):
    """Raised on internal server errors (HTTP 500)."""

    def __init__(
        self,
        message: str = "An unexpected server error occurred.",
        *,
        status_code: int = 500,
        error_code: str | None = "INTERNAL_SERVER_ERROR",
        correlation_id: str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            correlation_id=correlation_id,
            response_body=response_body,
        )


class EvalXServiceUnavailableError(EvalXError):
    """Raised when backend services or dependencies are down (HTTP 503)."""

    def __init__(
        self,
        message: str = "EVALX service is temporarily unavailable.",
        *,
        status_code: int = 503,
        error_code: str | None = "SERVICE_UNAVAILABLE",
        correlation_id: str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            correlation_id=correlation_id,
            response_body=response_body,
        )
