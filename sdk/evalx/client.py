"""Top-level client interfaces for the EVALX Python SDK."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from evalx.config import EvalXConfig
from evalx.models.analysis import RunAnalysis
from evalx.resources.audit import AsyncAuditResource, AuditResource
from evalx.resources.configurations import (
    AsyncConfigurationsResource,
    ConfigurationsResource,
)
from evalx.resources.datasets import AsyncDatasetsResource, DatasetsResource
from evalx.resources.evaluations import (
    AsyncEvaluationsResource,
    EvaluationsResource,
)
from evalx.resources.regression_gates import (
    AsyncRegressionGatesResource,
    RegressionGatesResource,
)
from evalx.resources.schedules import AsyncSchedulesResource, SchedulesResource
from evalx.transport import AsyncHttpTransport, HttpTransport


class EvalXClient:
    """Synchronous client for the EVALX AI Evaluation & Reliability Platform.

    Example:
        >>> from evalx import EvalXClient
        >>> client = EvalXClient(api_key="your_api_key", base_url="http://localhost:8000")
        >>> datasets = client.datasets.list()
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        connect_timeout: float = 10.0,
        max_retries: int = 3,
        retry_backoff_factor: float = 0.5,
        app_name: str | None = None,
        http_client: httpx.Client | None = None,
        config: EvalXConfig | None = None,
    ) -> None:
        if config is not None:
            self.config = config
        else:
            kwargs: dict[str, Any] = {
                "timeout": timeout,
                "connect_timeout": connect_timeout,
                "max_retries": max_retries,
                "retry_backoff_factor": retry_backoff_factor,
            }
            if base_url is not None:
                kwargs["base_url"] = base_url
            if api_key is not None:
                kwargs["api_key"] = api_key
            if app_name is not None:
                kwargs["app_name"] = app_name
            self.config = EvalXConfig(**kwargs)

        self._transport = HttpTransport(self.config, client=http_client)

        # Resource namespaces
        self.datasets = DatasetsResource(self._transport)
        self.evaluations = EvaluationsResource(self._transport)
        self.configurations = ConfigurationsResource(self._transport)
        self.regression_gates = RegressionGatesResource(self._transport)
        self.schedules = SchedulesResource(self._transport)
        self.audit = AuditResource(self._transport)

    def get_analysis(self, run_id: str | UUID) -> RunAnalysis:
        """Convenience shortcut to retrieve failure analysis for an evaluation run."""
        return self.evaluations.runs.get_analysis(run_id)

    def close(self) -> None:
        """Closes the underlying HTTP transport and connection pools."""
        self._transport.close()

    def __enter__(self) -> EvalXClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"EvalXClient(config={self.config!r})"


class AsyncEvalXClient:
    """Asynchronous client for the EVALX AI Evaluation & Reliability Platform.

    Example:
        >>> from evalx import AsyncEvalXClient
        >>> async with AsyncEvalXClient(api_key="your_api_key") as client:
        ...     datasets = await client.datasets.list()
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        connect_timeout: float = 10.0,
        max_retries: int = 3,
        retry_backoff_factor: float = 0.5,
        app_name: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        config: EvalXConfig | None = None,
    ) -> None:
        if config is not None:
            self.config = config
        else:
            kwargs: dict[str, Any] = {
                "timeout": timeout,
                "connect_timeout": connect_timeout,
                "max_retries": max_retries,
                "retry_backoff_factor": retry_backoff_factor,
            }
            if base_url is not None:
                kwargs["base_url"] = base_url
            if api_key is not None:
                kwargs["api_key"] = api_key
            if app_name is not None:
                kwargs["app_name"] = app_name
            self.config = EvalXConfig(**kwargs)

        self._transport = AsyncHttpTransport(self.config, client=http_client)

        # Resource namespaces
        self.datasets = AsyncDatasetsResource(self._transport)
        self.evaluations = AsyncEvaluationsResource(self._transport)
        self.configurations = AsyncConfigurationsResource(self._transport)
        self.regression_gates = AsyncRegressionGatesResource(self._transport)
        self.schedules = AsyncSchedulesResource(self._transport)
        self.audit = AsyncAuditResource(self._transport)

    async def get_analysis(self, run_id: str | UUID) -> RunAnalysis:
        """Convenience shortcut to retrieve failure analysis for an evaluation run."""
        return await self.evaluations.runs.get_analysis(run_id)

    async def aclose(self) -> None:
        """Asynchronously closes the underlying HTTP transport and connection pools."""
        await self._transport.aclose()

    async def __aenter__(self) -> AsyncEvalXClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        return f"AsyncEvalXClient(config={self.config!r})"
