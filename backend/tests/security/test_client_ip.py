from starlette.requests import Request

from app.security.client_ip import get_client_ip


def make_request(
    client_host: str | None = "192.168.1.100",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    client = (client_host, 12345) if client_host else None
    scope = {
        "type": "http",
        "client": client,
        "headers": headers or [],
    }
    return Request(scope)


def test_direct_client_ip_no_trusted_proxies() -> None:
    """When no trusted proxies are configured, direct client IP is always returned."""
    headers = [(b"x-forwarded-for", b"203.0.113.195")]
    req = make_request(client_host="198.51.100.25", headers=headers)
    ip = get_client_ip(req, trusted_proxies=None)
    assert ip == "198.51.100.25"

    req = make_request(client_host="198.51.100.25")
    ip = get_client_ip(req, trusted_proxies=None)
    assert ip == "198.51.100.25"


def test_untrusted_forwarded_header_spoofing() -> None:
    """Spoofed X-Forwarded-For is ignored when direct IP is not a trusted proxy."""
    # Attacker connects directly from 198.51.100.25, spoofing X-Forwarded-For
    headers = [(b"x-forwarded-for", b"203.0.113.195, 10.0.0.1")]
    req = make_request(client_host="198.51.100.25", headers=headers)

    trusted_proxies = ["10.0.0.1", "127.0.0.1"]
    ip = get_client_ip(req, trusted_proxies=trusted_proxies)

    # Must return the real attacker IP, strictly ignoring spoofed X-Forwarded-For
    assert ip == "198.51.100.25"


def test_trusted_proxy_mode_single_forward() -> None:
    """When direct connection is from trusted proxy, X-Forwarded-For is respected."""
    headers = [(b"x-forwarded-for", b"203.0.113.195")]
    req = make_request(client_host="10.0.0.1", headers=headers)

    trusted_proxies = ["10.0.0.1"]
    ip = get_client_ip(req, trusted_proxies=trusted_proxies)
    assert ip == "203.0.113.195"


def test_trusted_proxy_mode_multi_proxy_chain() -> None:
    """Walks backwards to find the first untrusted IP in the proxy chain."""
    # Chain: Client (203.0.113.50) -> Edge (10.0.0.2) -> Internal (10.0.0.1) -> EVALX
    headers = [(b"x-forwarded-for", b"203.0.113.50, 10.0.0.2")]
    req = make_request(client_host="10.0.0.1", headers=headers)

    trusted_proxies = ["10.0.0.1", "10.0.0.2"]
    ip = get_client_ip(req, trusted_proxies=trusted_proxies)
    assert ip == "203.0.113.50"


def test_trusted_proxy_mode_untrusted_intermediate() -> None:
    """If an intermediate hop is untrusted, it is identified as the client."""
    # Chain: 1.2.3.4 -> Untrusted (198.51.100.99) -> Trusted (10.0.0.1) -> EVALX
    headers = [(b"x-forwarded-for", b"1.2.3.4, 198.51.100.99")]
    req = make_request(client_host="10.0.0.1", headers=headers)

    trusted_proxies = ["10.0.0.1"]
    ip = get_client_ip(req, trusted_proxies=trusted_proxies)
    # 198.51.100.99 is the first untrusted IP from the right
    assert ip == "198.51.100.99"


def test_client_ip_missing_client() -> None:
    """Handles missing client gracefully."""
    req = make_request(client_host=None)
    ip = get_client_ip(req)
    assert ip == "127.0.0.1"
