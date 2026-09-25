"""Client configuration for the EVALX SDK."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class EvalXConfig:
    """Configuration options for EvalXClient and AsyncEvalXClient."""

    base_url: str = field(
        default_factory=lambda: os.getenv("EVALX_BASE_URL", "http://localhost:8000")
    )
    api_key: str | None = field(default_factory=lambda: os.getenv("EVALX_API_KEY"))
    timeout: float = 30.0
    connect_timeout: float = 10.0
    max_retries: int = 3
    retry_backoff_factor: float = 0.5
    app_name: str | None = None
    default_headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalize base_url: strip trailing slashes
        clean_url = self.base_url.rstrip("/")
        # If user did not supply /api/v1, append it
        if not clean_url.endswith("/api/v1"):
            clean_url = f"{clean_url}/api/v1"
        self.base_url = clean_url

        if self.app_name and len(self.app_name) > 64:
            self.app_name = self.app_name[:64]

        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if self.timeout <= 0:
            raise ValueError("timeout must be strictly positive")
        if self.connect_timeout <= 0:
            raise ValueError("connect_timeout must be strictly positive")

    def __repr__(self) -> str:
        masked_key = "***" if self.api_key else None
        return (
            f"EvalXConfig(base_url={self.base_url!r}, api_key={masked_key!r}, "
            f"timeout={self.timeout}, max_retries={self.max_retries}, "
            f"app_name={self.app_name!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()
