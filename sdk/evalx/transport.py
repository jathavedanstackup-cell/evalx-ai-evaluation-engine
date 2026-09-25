"""HTTP transport layer for the EVALX Python SDK."""

from __future__ import annotations

import asyncio
import logging
import platform
import time
import uuid
from typing import Any

import httpx

from evalx.config import EvalXConfig
from evalx.exceptions import (
    EvalXAuthenticationError,
    EvalXAuthorizationError,
    EvalXConflictError,
    EvalXConnectionError,
    EvalXError,
    EvalXNotFoundError,
    EvalXPayloadTooLargeError,
    EvalXRateLimitError,
    EvalXServerError,
    EvalXServiceUnavailableError,
    EvalXTimeoutError,
    EvalXValidationError,
)

logger = logging.getLogger("evalx.sdk.transport")
SDK_VERSION = "0.1.0"

IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


def _build_user_agent(app_name: str | None = None) -> str:
    py_ver = platform.python_version()
    os_name = platform.system()
    base = f"EvalX-Python/{SDK_VERSION} (Python {py_ver}; {os_name})"
    if app_name:
        return f"{base} {app_name}"
    return base


def _extract_error_detail(response: httpx.Response) -> tuple[str, str | None, Any]:
    """Safely extracts error info and raw detail from response JSON or text."""
    try:
        data = response.json()
    except Exception:
        text = response.text[:500] if response.text else "No response body"
        return text, None, text

    if isinstance(data, dict):
        detail = data.get("detail")
        error_code = data.get("error_code")
        if isinstance(detail, str):
            return detail, error_code, data
        elif isinstance(detail, list):
            # Typical FastAPI validation error
            return "Request validation failed", error_code or "VALIDATION_ERROR", data
        elif detail is not None:
            return str(detail), error_code, data
        return "Unknown error", error_code, data

    return str(data), None, data


def map_http_error(response: httpx.Response) -> EvalXError:
    """Deterministically maps HTTP status codes to typed EvalXError instances."""
    status_code = response.status_code
    correlation_id = response.headers.get("x-correlation-id")
    message, error_code, body = _extract_error_detail(response)

    if status_code == 401:
        return EvalXAuthenticationError(
            message,
            status_code=status_code,
            error_code=error_code or "UNAUTHORIZED",
            correlation_id=correlation_id,
            response_body=body,
        )
    elif status_code == 403:
        return EvalXAuthorizationError(
            message,
            status_code=status_code,
            error_code=error_code or "FORBIDDEN",
            correlation_id=correlation_id,
            response_body=body,
        )
    elif status_code == 404:
        return EvalXNotFoundError(
            message,
            status_code=status_code,
            error_code=error_code or "NOT_FOUND",
            correlation_id=correlation_id,
            response_body=body,
        )
    elif status_code == 409:
        return EvalXConflictError(
            message,
            status_code=status_code,
            error_code=error_code or "CONFLICT",
            correlation_id=correlation_id,
            response_body=body,
        )
    elif status_code == 413:
        return EvalXPayloadTooLargeError(
            message,
            status_code=status_code,
            error_code=error_code or "PAYLOAD_TOO_LARGE",
            correlation_id=correlation_id,
            response_body=body,
        )
    elif status_code == 422:
        raw_errors: list[dict[str, Any]] = []
        if isinstance(body, dict) and isinstance(body.get("detail"), list):
            raw_errors = [e for e in body["detail"] if isinstance(e, dict)]
        return EvalXValidationError(
            message,
            errors=raw_errors,
            status_code=status_code,
            error_code=error_code or "VALIDATION_ERROR",
            correlation_id=correlation_id,
            response_body=body,
        )
    elif status_code == 429:
        retry_after_hdr = response.headers.get("retry-after")
        retry_after: float | None = None
        if retry_after_hdr:
            try:
                retry_after = float(retry_after_hdr)
            except ValueError:
                pass
        if retry_after is None and isinstance(body, dict) and "retry_after" in body:
            try:
                retry_after = float(body["retry_after"])
            except (ValueError, TypeError):
                pass
        return EvalXRateLimitError(
            message,
            retry_after=retry_after,
            status_code=status_code,
            error_code=error_code or "RATE_LIMIT_EXCEEDED",
            correlation_id=correlation_id,
            response_body=body,
        )
    elif status_code == 500:
        return EvalXServerError(
            message,
            status_code=status_code,
            error_code=error_code or "INTERNAL_SERVER_ERROR",
            correlation_id=correlation_id,
            response_body=body,
        )
    elif status_code == 503:
        return EvalXServiceUnavailableError(
            message,
            status_code=status_code,
            error_code=error_code or "SERVICE_UNAVAILABLE",
            correlation_id=correlation_id,
            response_body=body,
        )
    else:
        return EvalXError(
            message,
            status_code=status_code,
            error_code=error_code,
            correlation_id=correlation_id,
            response_body=body,
        )


class BaseTransport:
    """Common request formatting and header preparation logic."""

    def __init__(self, config: EvalXConfig) -> None:
        self.config = config
        self.user_agent = _build_user_agent(config.app_name)

    def prepare_headers(
        self,
        custom_headers: dict[str, str] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        cid = correlation_id or str(uuid.uuid4())
        headers["X-Correlation-ID"] = cid

        # User-supplied default headers and per-request custom headers
        headers.update(self.config.default_headers)
        if custom_headers:
            headers.update(custom_headers)
        return headers

    def format_url(self, path: str) -> str:
        clean_path = path.lstrip("/")
        return f"{self.config.base_url}/{clean_path}"


class HttpTransport(BaseTransport):
    """Synchronous HTTP transport using httpx.Client with bounded retry backoff."""

    def __init__(
        self,
        config: EvalXConfig,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(config)
        timeout = httpx.Timeout(
            timeout=config.timeout,
            connect=config.connect_timeout,
        )
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpTransport:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        correlation_id: str | None = None,
        idempotent: bool = False,
    ) -> Any:
        url = self.format_url(path)
        req_headers = self.prepare_headers(headers, correlation_id)
        method_upper = method.upper()
        can_retry = idempotent or (method_upper in IDEMPOTENT_METHODS)

        attempt = 0
        last_exception: Exception | None = None

        while attempt <= self.config.max_retries:
            attempt += 1
            try:
                response = self._client.request(
                    method=method_upper,
                    url=url,
                    params=params,
                    json=json,
                    headers=req_headers,
                )

                if response.is_success:
                    if response.status_code == 204 or not response.content:
                        return None
                    return response.json()

                # Handle retryable HTTP errors
                if can_retry and response.status_code in RETRYABLE_STATUS_CODES:
                    if attempt <= self.config.max_retries:
                        backoff = self._compute_backoff(attempt, response)
                        time.sleep(backoff)
                        continue

                raise map_http_error(response)

            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_exception = exc
                if can_retry and attempt <= self.config.max_retries:
                    time.sleep(self.config.retry_backoff_factor * (2 ** (attempt - 1)))
                    continue
                raise EvalXConnectionError(
                    f"Failed to connect to EVALX server at {url}: {exc}",
                    correlation_id=req_headers.get("X-Correlation-ID"),
                ) from exc

            except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
                last_exception = exc
                if can_retry and attempt <= self.config.max_retries:
                    time.sleep(self.config.retry_backoff_factor * (2 ** (attempt - 1)))
                    continue
                raise EvalXTimeoutError(
                    f"Request to {url} timed out: {exc}",
                    correlation_id=req_headers.get("X-Correlation-ID"),
                ) from exc

        if last_exception:
            raise EvalXError(
                f"Request failed after retries: {last_exception}"
            ) from last_exception

    def _compute_backoff(self, attempt: int, response: httpx.Response) -> float:
        if response.status_code == 429:
            hdr = response.headers.get("retry-after")
            if hdr:
                try:
                    return min(float(hdr), 30.0)
                except ValueError:
                    pass
        return min(self.config.retry_backoff_factor * (2 ** (attempt - 1)), 10.0)


class AsyncHttpTransport(BaseTransport):
    """Asynchronous HTTP transport with bounded retry backoff."""

    def __init__(
        self,
        config: EvalXConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config)
        timeout = httpx.Timeout(
            timeout=config.timeout,
            connect=config.connect_timeout,
        )
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> AsyncHttpTransport:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        correlation_id: str | None = None,
        idempotent: bool = False,
    ) -> Any:
        url = self.format_url(path)
        req_headers = self.prepare_headers(headers, correlation_id)
        method_upper = method.upper()
        can_retry = idempotent or (method_upper in IDEMPOTENT_METHODS)

        attempt = 0
        last_exception: Exception | None = None

        while attempt <= self.config.max_retries:
            attempt += 1
            try:
                response = await self._client.request(
                    method=method_upper,
                    url=url,
                    params=params,
                    json=json,
                    headers=req_headers,
                )

                if response.is_success:
                    if response.status_code == 204 or not response.content:
                        return None
                    return response.json()

                if can_retry and response.status_code in RETRYABLE_STATUS_CODES:
                    if attempt <= self.config.max_retries:
                        backoff = self._compute_backoff(attempt, response)
                        await asyncio.sleep(backoff)
                        continue

                raise map_http_error(response)

            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_exception = exc
                if can_retry and attempt <= self.config.max_retries:
                    await asyncio.sleep(
                        self.config.retry_backoff_factor * (2 ** (attempt - 1))
                    )
                    continue
                raise EvalXConnectionError(
                    f"Failed to connect to EVALX server at {url}: {exc}",
                    correlation_id=req_headers.get("X-Correlation-ID"),
                ) from exc

            except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
                last_exception = exc
                if can_retry and attempt <= self.config.max_retries:
                    await asyncio.sleep(
                        self.config.retry_backoff_factor * (2 ** (attempt - 1))
                    )
                    continue
                raise EvalXTimeoutError(
                    f"Request to {url} timed out: {exc}",
                    correlation_id=req_headers.get("X-Correlation-ID"),
                ) from exc

        if last_exception:
            raise EvalXError(
                f"Request failed after retries: {last_exception}"
            ) from last_exception

    def _compute_backoff(self, attempt: int, response: httpx.Response) -> float:
        if response.status_code == 429:
            hdr = response.headers.get("retry-after")
            if hdr:
                try:
                    return min(float(hdr), 30.0)
                except ValueError:
                    pass
        return min(self.config.retry_backoff_factor * (2 ** (attempt - 1)), 10.0)
