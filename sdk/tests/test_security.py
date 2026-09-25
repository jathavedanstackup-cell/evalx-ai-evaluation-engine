"""Security and non-leakage tests for the EVALX SDK."""

from __future__ import annotations

import logging

import httpx
import pytest

from evalx.client import EvalXClient
from evalx.config import EvalXConfig
from evalx.exceptions import EvalXAuthenticationError


def test_token_not_logged_during_requests(caplog: pytest.LogCaptureFixture) -> None:
    secret_token = "ultra_sensitive_production_token_abc123"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = EvalXConfig(base_url="http://testserver", api_key=secret_token)

    with caplog.at_level(logging.DEBUG):
        client = EvalXClient(config=cfg, http_client=mock_client)
        client.datasets.list()

    log_text = caplog.text
    assert secret_token not in log_text


def test_exception_string_omits_raw_tokens() -> None:
    secret_token = "secret_bearer_98765"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"detail": "Unauthorized: Token verification failed"},
            headers={"x-correlation-id": "cid-secure-01"},
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = EvalXClient(
        base_url="http://testserver",
        api_key=secret_token,
        http_client=mock_client,
    )

    with pytest.raises(EvalXAuthenticationError) as exc_info:
        client.datasets.create(name="Should Fail")

    err = exc_info.value
    err_str = str(err)
    err_repr = repr(err)

    assert secret_token not in err_str
    assert secret_token not in err_repr
    assert err.correlation_id == "cid-secure-01"
