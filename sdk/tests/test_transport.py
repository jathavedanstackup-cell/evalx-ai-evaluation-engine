"""Unit tests for the EVALX SDK HTTP transport layer."""

from __future__ import annotations

import httpx
import pytest

from evalx.config import EvalXConfig
from evalx.exceptions import (
    EvalXConnectionError,
    EvalXTimeoutError,
)
from evalx.transport import (
    AsyncHttpTransport,
    HttpTransport,
    _build_user_agent,
)


def test_user_agent_building() -> None:
    ua = _build_user_agent()
    assert "EvalX-Python/" in ua
    assert "Python" in ua

    ua_custom = _build_user_agent("MyApp/2.0")
    assert "MyApp/2.0" in ua_custom


def test_base_url_normalization() -> None:
    cfg1 = EvalXConfig(base_url="http://api.evalx.dev")
    assert cfg1.base_url == "http://api.evalx.dev/api/v1"

    cfg2 = EvalXConfig(base_url="http://api.evalx.dev/api/v1/")
    assert cfg2.base_url == "http://api.evalx.dev/api/v1"


def test_transport_prepare_headers() -> None:
    cfg = EvalXConfig(
        base_url="http://localhost:8000",
        api_key="test-token-123",
        default_headers={"X-Custom": "custom-val"},
    )
    transport = HttpTransport(cfg)
    headers = transport.prepare_headers(
        custom_headers={"X-Extra": "extra-val"},
        correlation_id="cid-999",
    )
    assert headers["Authorization"] == "Bearer test-token-123"
    assert headers["X-Correlation-ID"] == "cid-999"
    assert headers["X-Custom"] == "custom-val"
    assert headers["X-Extra"] == "extra-val"
    assert "EvalX-Python/" in headers["User-Agent"]


def test_transport_success_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/test"
        assert request.headers["Authorization"] == "Bearer secret-key"
        assert "X-Correlation-ID" in request.headers
        return httpx.Response(200, json={"status": "ok", "count": 42})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = EvalXConfig(base_url="http://testserver", api_key="secret-key")
    transport = HttpTransport(cfg, client=mock_client)

    res = transport.request("GET", "test")
    assert res == {"status": "ok", "count": 42}


def test_transport_retries_idempotent_get_on_503() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(503, json={"detail": "Service unavailable"})
        return httpx.Response(200, json={"result": "success"})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = EvalXConfig(
        base_url="http://testserver",
        max_retries=3,
        retry_backoff_factor=0.01,
    )
    transport = HttpTransport(cfg, client=mock_client)

    res = transport.request("GET", "items")
    assert res == {"result": "success"}
    assert call_count == 3


def test_transport_never_retries_unsafe_post() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(503, json={"detail": "Service unavailable"})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = EvalXConfig(
        base_url="http://testserver",
        max_retries=3,
        retry_backoff_factor=0.01,
    )
    transport = HttpTransport(cfg, client=mock_client)

    with pytest.raises(Exception) as exc_info:
        transport.request("POST", "runs", json={"evaluation_id": "123"})

    # Post must NOT be retried: call_count must be exactly 1
    assert call_count == 1
    assert "unavailable" in str(exc_info.value).lower()


def test_transport_connect_error_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Network unreachable", request=request)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = EvalXConfig(base_url="http://offline-server", max_retries=0)
    transport = HttpTransport(cfg, client=mock_client)

    with pytest.raises(EvalXConnectionError) as exc_info:
        transport.request("GET", "ping")
    assert "Failed to connect" in str(exc_info.value)


def test_transport_timeout_error_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Server timed out", request=request)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = EvalXConfig(base_url="http://slow-server", max_retries=0)
    transport = HttpTransport(cfg, client=mock_client)

    with pytest.raises(EvalXTimeoutError) as exc_info:
        transport.request("GET", "slow")
    assert "timed out" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_async_transport_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/async-test"
        return httpx.Response(200, json={"async": True})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = EvalXConfig(base_url="http://testserver")
    transport = AsyncHttpTransport(cfg, client=mock_client)

    res = await transport.request("GET", "async-test")
    assert res == {"async": True}
