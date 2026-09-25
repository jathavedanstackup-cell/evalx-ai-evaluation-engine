import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.evaluation.errors import sanitize_error_message
from app.observability.correlation import validate_and_normalize_correlation_id
from app.observability.metrics import default_metrics_collector
from app.security.middleware import PayloadTooLargeError
from app.security.rate_limiter import (
    RateLimiterUnavailableError,
    RateLimitExceededError,
)

logger = logging.getLogger(__name__)


def _get_correlation_id(request: Request) -> str:
    """Extracts or derives a safe correlation ID for the request."""
    cid = getattr(request.state, "correlation_id", None)
    if not cid:
        raw_cid = request.headers.get("x-correlation-id")
        cid = validate_and_normalize_correlation_id(raw_cid)
    return cid


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Hardened handler for HTTPExceptions, preserving safe details and CIDs."""
    cid = _get_correlation_id(request)
    headers = dict(exc.headers or {})
    headers["X-Correlation-ID"] = cid

    if isinstance(exc.detail, str):
        content: dict[str, Any] = {
            "detail": sanitize_error_message(exc.detail),
        }
    elif isinstance(exc.detail, dict):
        content = dict(exc.detail)
        if "correlation_id" not in content:
            content["correlation_id"] = cid
    else:
        content = {"detail": exc.detail}

    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers=headers,
    )


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceededError
) -> JSONResponse:
    """Handler for 429 Too Many Requests."""
    default_metrics_collector.record_rate_limit_hit()
    cid = _get_correlation_id(request)
    headers = {
        "Retry-After": str(exc.result.retry_after),
        "X-RateLimit-Limit": str(exc.result.limit),
        "X-RateLimit-Remaining": str(exc.result.remaining),
        "X-RateLimit-Reset": str(exc.result.reset),
        "X-Correlation-ID": cid,
    }
    if exc.result.degraded:
        headers["X-RateLimit-Degraded"] = "true"

    content = {
        "detail": (
            f"Too many requests. Please retry after {exc.result.retry_after} seconds."
        ),
        "error_code": "RATE_LIMIT_EXCEEDED",
        "retry_after": exc.result.retry_after,
        "correlation_id": cid,
    }
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content=content,
        headers=headers,
    )


async def rate_limiter_unavailable_handler(
    request: Request, exc: RateLimiterUnavailableError
) -> JSONResponse:
    """Handler for 503 when expensive rate-limited tier fails closed."""
    default_metrics_collector.record_rate_limiter_degraded()
    cid = _get_correlation_id(request)
    headers = {"X-Correlation-ID": cid}
    content = {
        "detail": "Rate limiter is currently unavailable. Request rejected for safety.",
        "error_code": "RATE_LIMITER_UNAVAILABLE",
        "correlation_id": cid,
    }
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=content,
        headers=headers,
    )


async def payload_too_large_handler(
    request: Request, exc: PayloadTooLargeError
) -> JSONResponse:
    """Handler for 413 Payload Too Large."""
    default_metrics_collector.record_request_body_rejection()
    cid = _get_correlation_id(request)
    headers = {"X-Correlation-ID": cid}
    content = {
        "detail": str(exc) or "Request payload exceeds maximum allowable limit.",
        "error_code": "PAYLOAD_TOO_LARGE",
        "correlation_id": cid,
    }
    return JSONResponse(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        content=content,
        headers=headers,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handler for 422 Unprocessable Entity, attaching correlation ID."""
    from fastapi.encoders import jsonable_encoder

    cid = _get_correlation_id(request)
    headers = {"X-Correlation-ID": cid}
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": jsonable_encoder(exc.errors())},
        headers=headers,
    )


async def sqlalchemy_exception_handler(
    request: Request, exc: SQLAlchemyError
) -> JSONResponse:
    """Handler for database errors without leaking queries, parameters, or schema."""
    cid = _get_correlation_id(request)

    # Safe structured logging: NEVER log raw queries, bound params, or user data
    logger.error(
        "Database error: correlation_id=%s path=%s exception_class=%s "
        "safe_error=%s operation_category=DATABASE_ERROR",
        cid,
        request.url.path,
        exc.__class__.__name__,
        sanitize_error_message(str(exc)),
        exc_info=True,
    )

    headers = {"X-Correlation-ID": cid}
    content = {
        "detail": "An internal database error occurred.",
        "error_code": "DATABASE_ERROR",
        "correlation_id": cid,
    }
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=content,
        headers=headers,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unhandled exceptions to prevent traceback leakage."""
    cid = _get_correlation_id(request)

    logger.error(
        "Unhandled internal server error: correlation_id=%s path=%s exception=%s",
        cid,
        request.url.path,
        sanitize_error_message(str(exc)),
        exc_info=True,
    )

    headers = {"X-Correlation-ID": cid}
    content = {
        "detail": "An unexpected internal error occurred.",
        "error_code": "INTERNAL_SERVER_ERROR",
        "correlation_id": cid,
    }
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=content,
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Registers hardened global exception handlers on FastAPI application."""
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RateLimitExceededError, rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        RateLimiterUnavailableError,
        rate_limiter_unavailable_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(PayloadTooLargeError, payload_too_large_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
