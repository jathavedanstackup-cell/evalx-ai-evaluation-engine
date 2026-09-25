"""Central structured logging policy and sanitizing utilities for EVALX.

Enforces strict redaction of sensitive credentials, database/Redis URLs,
raw SQL queries, model prompts, and candidate responses across all log sinks.
"""

from __future__ import annotations

import logging
import re

from app.observability.sanitization import sanitize_for_observability

# Redaction patterns for inline log message strings
_INLINE_PATTERNS = [
    # Passwords and secrets in connection strings (postgresql, redis, etc.)
    (
        re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^:@\s]*:)([^@\s]+)(@)", re.IGNORECASE),
        r"\1[REDACTED]\3",
    ),
    # Bearer tokens
    (
        re.compile(r"(bearer\s+)[a-zA-Z0-9\-_.]+", re.IGNORECASE),
        r"\1[REDACTED_TOKEN]",
    ),
    # API keys / secret parameters
    (
        re.compile(
            r"((?:api[_-]?key|secret|password|token)\s*[=:]\s*['\"]?)[^'\",\s]+(['\"]?)",
            re.IGNORECASE,
        ),
        r"\1[REDACTED]\2",
    ),
    # Raw SQL parameter bindings (e.g. parameters: (...))
    (
        re.compile(r"(\[parameters:\s*)(?:\{[^}]*\}|\([^)]*\))(\])", re.IGNORECASE),
        r"\1[REDACTED_SQL_PARAMS]\2",
    ),
]


def redact_sensitive_string(message: str) -> str:
    """Scrubs credential patterns, connection strings, and tokens from a text string."""
    if not message:
        return message
    result = message
    for pattern, replacement in _INLINE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class SensitiveDataFilter(logging.Filter):
    """Logging filter that automatically redacts sensitive data from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact main message if it is a string
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_string(record.msg)

        # Redact args if present
        if record.args:
            if isinstance(record.args, dict):
                record.args = sanitize_for_observability(record.args, omit_prompts=True)
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_sensitive_string(str(arg))
                    if isinstance(arg, str)
                    else (
                        sanitize_for_observability(arg, omit_prompts=True)
                        if isinstance(arg, dict)
                        else arg
                    )
                    for arg in record.args
                )

        # Redact evalx_event extra payload if present
        evalx_event = getattr(record, "evalx_event", None)
        if isinstance(evalx_event, dict):
            record.evalx_event = sanitize_for_observability(
                evalx_event, omit_prompts=True
            )

        return True


def get_structured_logger(name: str = "evalx") -> logging.Logger:
    """Returns a logger pre-configured with the sensitive data redaction filter."""
    logger = logging.getLogger(name)
    has_filter = any(isinstance(f, SensitiveDataFilter) for f in logger.filters)
    if not has_filter:
        logger.addFilter(SensitiveDataFilter())
    return logger
