import json
from typing import Any

SENSITIVE_SUBSTRINGS = (
    "api_key",
    "apikey",
    "token",
    "authorization",
    "password",
    "secret",
    "credential",
    "access_key",
    "private_key",
    "auth",
    "bearer",
)

PROMPT_RESPONSE_KEYS = (
    "prompt",
    "response",
    "actual_output",
    "expected_output",
    "input",
    "context",
)

MAX_METADATA_SERIALIZED_BYTES = 16_384  # 16 KB
MAX_METADATA_DEPTH = 3
MAX_METADATA_KEYS = 50


def is_sensitive_key(key: str) -> bool:
    """Checks if key contains any sensitive credential or secret substring."""
    k_lower = str(key).lower().strip()
    return any(sub in k_lower for sub in SENSITIVE_SUBSTRINGS)


def is_prompt_response_key(key: str) -> bool:
    """Checks if key represents raw prompt or model response content."""
    k_lower = str(key).lower().strip()
    return k_lower in PROMPT_RESPONSE_KEYS


def sanitize_for_observability(
    obj: Any,
    omit_prompts: bool = True,
    depth: int = 0,
    max_depth: int = 5,
) -> Any:
    """Recursively redacts sensitive credentials and omits raw prompts/responses."""
    if depth > max_depth:
        return "[DEPTH_EXCEEDED]"

    if isinstance(obj, dict):
        sanitized: dict[str, Any] = {}
        for k, v in obj.items():
            k_str = str(k)
            if is_sensitive_key(k_str):
                sanitized[k_str] = "[REDACTED]"
            elif omit_prompts and is_prompt_response_key(k_str):
                sanitized[k_str] = "[OMITTED]"
            elif isinstance(v, (dict, list)):
                sanitized[k_str] = sanitize_for_observability(
                    v, omit_prompts=omit_prompts, depth=depth + 1, max_depth=max_depth
                )
            else:
                sanitized[k_str] = v
        return sanitized

    if isinstance(obj, list):
        return [
            sanitize_for_observability(
                item, omit_prompts=omit_prompts, depth=depth + 1, max_depth=max_depth
            )
            for item in obj
        ]

    return obj


def _get_dict_depth(d: Any, current_depth: int = 1) -> int:
    if not isinstance(d, dict) or not d:
        return current_depth
    return (
        max(
            _get_dict_depth(v, current_depth + 1)
            for v in d.values()
            if isinstance(v, (dict, list))
        )
        if any(isinstance(v, (dict, list)) for v in d.values())
        else current_depth
    )


def validate_execution_metadata(
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Validates bounded operational execution metadata.

    Enforces limits:
      - Max keys: 50
      - Max nesting depth: 3
      - Max serialized size: 16 KB
    """
    if metadata is None:
        return None

    if not isinstance(metadata, dict):
        raise ValueError("execution_metadata must be a dictionary")

    if len(metadata) > MAX_METADATA_KEYS:
        raise ValueError(
            f"execution_metadata exceeds maximum of {MAX_METADATA_KEYS} keys, "
            f"got {len(metadata)}"
        )

    depth = _get_dict_depth(metadata)
    if depth > MAX_METADATA_DEPTH:
        raise ValueError(
            f"execution_metadata exceeds maximum nesting depth of "
            f"{MAX_METADATA_DEPTH}, got {depth}"
        )

    try:
        serialized = json.dumps(metadata, default=str)
    except Exception as exc:
        raise ValueError(f"execution_metadata is not JSON-serializable: {exc}") from exc

    if len(serialized.encode("utf-8")) > MAX_METADATA_SERIALIZED_BYTES:
        raise ValueError(
            f"execution_metadata exceeds maximum serialized size of "
            f"{MAX_METADATA_SERIALIZED_BYTES} bytes"
        )

    return metadata
