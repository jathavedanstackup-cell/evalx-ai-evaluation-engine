import re
import uuid

CORRELATION_ID_MAX_LENGTH = 100
_SAFE_CORRELATION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.:]{1,100}$")


def validate_and_normalize_correlation_id(raw_id: str | None) -> str:
    """Validates an incoming client correlation ID or generates a safe one.

    Rules:
      - Maximum 100 characters.
      - Rejects CR/LF, null bytes, whitespace, and control characters
        to prevent log injection.
      - Returns a fresh server-side correlation ID ('evalx-<uuid16>') if raw_id
        is missing or invalid.
    """

    if raw_id is None:
        return f"evalx-{uuid.uuid4().hex[:16]}"

    stripped = raw_id.strip()
    if not stripped or len(stripped) > CORRELATION_ID_MAX_LENGTH:
        return f"evalx-{uuid.uuid4().hex[:16]}"

    if not _SAFE_CORRELATION_ID_REGEX.match(stripped):
        return f"evalx-{uuid.uuid4().hex[:16]}"

    return stripped
