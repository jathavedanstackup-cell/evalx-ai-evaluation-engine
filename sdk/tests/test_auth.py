"""Unit tests for SDK authentication handling and security boundaries."""

from __future__ import annotations

import httpx
import pytest

from evalx.client import EvalXClient
from evalx.config import EvalXConfig
from evalx.exceptions import EvalXAuthenticationError
from evalx.transport import HttpTransport


def test_api_key_masked_in_repr() -> None:
    secret = "sk_live_super_secret_evalx_key_999"
    cfg = EvalXConfig(api_key=secret)

    cfg_repr = repr(cfg)
    cfg_str = str(cfg)
    assert secret not in cfg_repr
    assert secret not in cfg_str
    assert "***" in cfg_repr

    client = EvalXClient(api_key=secret)
    client_repr = repr(client)
    assert secret not in client_repr
    assert "***" in client_repr


def test_bearer_token_injection_in_headers() -> None:
    secret = "valid_jwt_or_bearer_token"
    captured_auth: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_auth
        captured_auth = request.headers.get("Authorization")
        return httpx.Response(200, json={"authenticated": True})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = EvalXConfig(base_url="http://testserver", api_key=secret)
    transport = HttpTransport(cfg, client=mock_client)

    transport.request("GET", "me")
    assert captured_auth == f"Bearer {secret}"


def test_auth_failure_raises_typed_exception_without_leaking_key() -> None:
    secret = "leaked_secret_token_12345"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"detail": "Unauthorized: Invalid authentication credentials"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = EvalXClient(
        base_url="http://testserver",
        api_key=secret,
        http_client=mock_client,
    )

    with pytest.raises(EvalXAuthenticationError) as exc_info:
        client.datasets.list()

    err = exc_info.value
    assert err.status_code == 401
    assert "Invalid authentication credentials" in err.message
    # Critical security assertion: secret must NEVER appear in the
    # exception string or repr
    assert secret not in str(err)
    assert secret not in repr(err)


def test_config_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVALX_API_KEY", "env_token_abc")
    monkeypatch.setenv("EVALX_BASE_URL", "https://evalx.example.com")

    cfg = EvalXConfig()
    assert cfg.api_key == "env_token_abc"
    assert cfg.base_url == "https://evalx.example.com/api/v1"
