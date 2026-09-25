from starlette.requests import Request


def get_client_ip(request: Request, trusted_proxies: list[str] | None = None) -> str:
    """Extracts client IP address safely without blindly trusting forwarded headers.

    Rules:
      - If request.client is None or empty, returns '127.0.0.1'.
      - direct_ip = request.client.host.
      - If trusted_proxies is configured AND direct_ip is in trusted_proxies:
          Parses 'X-Forwarded-For' header.
          Walks backwards from right-to-left to find the first untrusted IP.
          If all IPs in the forwarded chain are trusted, returns the leftmost IP.
      - Otherwise:
          Returns direct_ip, strictly ignoring 'X-Forwarded-For' to prevent spoofing.
    """
    if request.client is None or not request.client.host:
        return "127.0.0.1"

    direct_ip = request.client.host.strip()

    if not trusted_proxies or direct_ip not in trusted_proxies:
        return direct_ip

    xff = request.headers.get("x-forwarded-for")
    if not xff:
        return direct_ip

    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if not parts:
        return direct_ip

    for ip in reversed(parts):
        if ip not in trusted_proxies:
            return ip

    return parts[0]
