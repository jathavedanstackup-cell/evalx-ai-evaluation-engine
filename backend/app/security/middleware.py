import logging
import time

from fastapi import HTTPException, status
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.observability.correlation import validate_and_normalize_correlation_id
from app.observability.metrics import default_metrics_collector

logger = logging.getLogger(__name__)


class PayloadTooLargeError(HTTPException):
    """Raised when incoming request payload exceeds configured byte limits."""

    def __init__(
        self,
        detail: str = "Request payload exceeds maximum allowable limit.",
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=detail,
            headers={"X-Correlation-ID": correlation_id} if correlation_id else None,
        )
        self.correlation_id = correlation_id


class CorrelationIdMiddleware:
    """Extracts or generates safe X-Correlation-ID for requests and responses."""

    """Extracts or generates safe X-Correlation-ID and records API request metrics."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()
        headers = dict(scope.get("headers", []))
        raw_cid = headers.get(b"x-correlation-id")
        cid_str = raw_cid.decode("latin1") if raw_cid else None
        correlation_id = validate_and_normalize_correlation_id(cid_str)

        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["correlation_id"] = correlation_id

        status_code = 200

        async def send_with_cid(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 200))
                resp_headers = list(message.get("headers", []))
                has_cid = any(h[0].lower() == b"x-correlation-id" for h in resp_headers)
                if not has_cid:
                    resp_headers.append(
                        (b"x-correlation-id", correlation_id.encode("latin1"))
                    )
                message["headers"] = resp_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_cid)
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            default_metrics_collector.record_request(
                method=str(scope.get("method", "GET")),
                status_code=status_code,
                duration_ms=duration_ms,
            )


class SecurityHeadersMiddleware:
    """Enforces non-prescriptive API baseline security headers and Cache-Control."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not settings.security_headers_enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                resp_headers = list(message.get("headers", []))
                existing_names = {h[0].lower() for h in resp_headers}

                if b"x-content-type-options" not in existing_names:
                    resp_headers.append((b"x-content-type-options", b"nosniff"))
                if b"x-frame-options" not in existing_names:
                    resp_headers.append((b"x-frame-options", b"DENY"))
                if b"referrer-policy" not in existing_names:
                    resp_headers.append(
                        (b"referrer-policy", b"strict-origin-when-cross-origin")
                    )
                if b"permissions-policy" not in existing_names:
                    resp_headers.append(
                        (
                            b"permissions-policy",
                            b"accelerometer=(), camera=(), geolocation=(), "
                            b"gyroscope=(), magnetometer=(), microphone=(), "
                            b"payment=(), usb=()",
                        )
                    )
                if (
                    settings.hsts_enabled
                    and b"strict-transport-security" not in existing_names
                ):
                    resp_headers.append(
                        (
                            b"strict-transport-security",
                            b"max-age=31536000; includeSubDomains",
                        )
                    )

                # Response Cache-Control: applied to sensitive authenticated endpoints
                if path.startswith(("/api/v1/datasets", "/api/v1/evaluations")):
                    if b"cache-control" not in existing_names:
                        resp_headers.append((b"cache-control", b"no-store"))

                message["headers"] = resp_headers
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class RequestSizeLimitMiddleware:
    """Enforces maximum request payload limits without second-pass body parsing."""

    def __init__(self, app: ASGIApp, max_body_bytes: int | None = None) -> None:
        self.app = app
        self._max_body_bytes = max_body_bytes

    @property
    def max_body_bytes(self) -> int:
        if self._max_body_bytes is not None:
            return self._max_body_bytes
        return settings.max_request_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length_raw = headers.get(b"content-length")

        # 1. Content-Length check: reject oversized payloads immediately
        if content_length_raw is not None:
            try:
                content_length = int(content_length_raw.decode("latin1"))
                if content_length > self.max_body_bytes:
                    cid = scope.get("state", {}).get("correlation_id")
                    if not cid:
                        raw_cid = headers.get(b"x-correlation-id")
                        cid = validate_and_normalize_correlation_id(
                            raw_cid.decode("latin1") if raw_cid else None
                        )
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "detail": (
                                f"Request payload exceeds maximum allowable limit "
                                f"of {self.max_body_bytes} bytes."
                            ),
                            "error_code": "PAYLOAD_TOO_LARGE",
                            "correlation_id": cid,
                        },
                        headers={"X-Correlation-ID": cid},
                    )
                    await response(scope, receive, send)
                    return
            except ValueError:
                pass

        # 2. Bounded receive wrapper for streaming/chunked requests
        # without Content-Length
        total_bytes_received = 0

        async def bounded_receive() -> Message:
            nonlocal total_bytes_received
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                total_bytes_received += len(chunk)
                if total_bytes_received > self.max_body_bytes:
                    msg = (
                        f"Request payload exceeds maximum allowable limit of "
                        f"{self.max_body_bytes} bytes."
                    )
                    raise PayloadTooLargeError(msg)
            return message

        try:
            await self.app(scope, bounded_receive, send)
        except PayloadTooLargeError:
            cid = scope.get("state", {}).get("correlation_id")
            if not cid:
                raw_cid = headers.get(b"x-correlation-id")
                cid = validate_and_normalize_correlation_id(
                    raw_cid.decode("latin1") if raw_cid else None
                )
            response = JSONResponse(
                status_code=413,
                content={
                    "detail": (
                        f"Request payload exceeds maximum allowable limit "
                        f"of {self.max_body_bytes} bytes."
                    ),
                    "error_code": "PAYLOAD_TOO_LARGE",
                    "correlation_id": cid,
                },
                headers={"X-Correlation-ID": cid},
            )
            await response(scope, receive, send)
