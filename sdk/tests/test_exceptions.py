"""Unit tests for SDK exception mapping and error contracts."""

from __future__ import annotations

import httpx

from evalx.exceptions import (
    EvalXAuthenticationError,
    EvalXAuthorizationError,
    EvalXConflictError,
    EvalXError,
    EvalXNotFoundError,
    EvalXPayloadTooLargeError,
    EvalXRateLimitError,
    EvalXServerError,
    EvalXServiceUnavailableError,
    EvalXValidationError,
)
from evalx.transport import map_http_error


def test_map_401_unauthorized() -> None:
    res = httpx.Response(
        401,
        json={"detail": "Invalid token", "error_code": "TOKEN_INVALID"},
        headers={"x-correlation-id": "cid-401"},
    )
    err = map_http_error(res)
    assert isinstance(err, EvalXAuthenticationError)
    assert err.status_code == 401
    assert err.error_code == "TOKEN_INVALID"
    assert err.correlation_id == "cid-401"
    assert "Invalid token" in err.message


def test_map_403_forbidden() -> None:
    res = httpx.Response(
        403,
        json={"detail": "Tenant boundary violation", "error_code": "FORBIDDEN"},
        headers={"x-correlation-id": "cid-403"},
    )
    err = map_http_error(res)
    assert isinstance(err, EvalXAuthorizationError)
    assert err.status_code == 403
    assert err.correlation_id == "cid-403"


def test_map_404_not_found() -> None:
    res = httpx.Response(
        404,
        json={"detail": "Dataset not found", "error_code": "NOT_FOUND"},
        headers={"x-correlation-id": "cid-404"},
    )
    err = map_http_error(res)
    assert isinstance(err, EvalXNotFoundError)
    assert err.status_code == 404
    assert err.correlation_id == "cid-404"


def test_map_409_conflict() -> None:
    res = httpx.Response(
        409,
        json={"detail": "Name already exists", "error_code": "CONFLICT"},
        headers={"x-correlation-id": "cid-409"},
    )
    err = map_http_error(res)
    assert isinstance(err, EvalXConflictError)
    assert err.status_code == 409


def test_map_413_payload_too_large() -> None:
    res = httpx.Response(
        413,
        json={"detail": "Payload too large", "error_code": "PAYLOAD_TOO_LARGE"},
    )
    err = map_http_error(res)
    assert isinstance(err, EvalXPayloadTooLargeError)
    assert err.status_code == 413


def test_map_422_validation_error() -> None:
    errors = [{"loc": ["body", "name"], "msg": "Field required", "type": "missing"}]
    res = httpx.Response(
        422,
        json={"detail": errors},
        headers={"x-correlation-id": "cid-422"},
    )
    err = map_http_error(res)
    assert isinstance(err, EvalXValidationError)
    assert err.status_code == 422
    assert len(err.errors) == 1
    assert err.errors[0]["loc"] == ["body", "name"]
    assert err.correlation_id == "cid-422"


def test_map_429_rate_limit_with_retry_after() -> None:
    res = httpx.Response(
        429,
        json={"detail": "Too many requests", "retry_after": 15.5},
        headers={"retry-after": "15.5", "x-correlation-id": "cid-429"},
    )
    err = map_http_error(res)
    assert isinstance(err, EvalXRateLimitError)
    assert err.status_code == 429
    assert err.retry_after == 15.5
    assert err.correlation_id == "cid-429"


def test_map_500_server_error() -> None:
    res = httpx.Response(
        500,
        json={"detail": "Internal database error", "error_code": "DATABASE_ERROR"},
        headers={"x-correlation-id": "cid-500"},
    )
    err = map_http_error(res)
    assert isinstance(err, EvalXServerError)
    assert err.status_code == 500
    assert err.error_code == "DATABASE_ERROR"


def test_map_503_service_unavailable() -> None:
    res = httpx.Response(
        503,
        json={"detail": "Database unavailable", "error_code": "SERVICE_UNAVAILABLE"},
        headers={"x-correlation-id": "cid-503"},
    )
    err = map_http_error(res)
    assert isinstance(err, EvalXServiceUnavailableError)
    assert err.status_code == 503


def test_map_non_json_error_body() -> None:
    res = httpx.Response(
        502,
        text="<html>Bad Gateway</html>",
        headers={"x-correlation-id": "cid-502"},
    )
    err = map_http_error(res)
    assert isinstance(err, EvalXError)
    assert err.status_code == 502
    assert "Bad Gateway" in err.message
