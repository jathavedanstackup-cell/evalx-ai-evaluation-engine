"""Regression gates resource clients (synchronous and asynchronous)."""

from __future__ import annotations

import builtins
from typing import Any
from uuid import UUID

from evalx.models.regression_gate import (
    GateEvaluationResult,
    GateRule,
    RegressionGate,
    RegressionGateVersion,
)
from evalx.pagination import Page
from evalx.resources.base import AsyncResource, SyncResource


def _serialize_gate_rules(
    rules: list[GateRule | dict[str, Any]],
) -> list[dict[str, Any]]:
    op_map = {
        ">=": "gte",
        ">": "gt",
        "<=": "lte",
        "<": "lt",
        "==": "eq",
        "=": "eq",
    }
    serialized: list[dict[str, Any]] = []
    for r in rules:
        if isinstance(r, GateRule):
            op = op_map.get(r.operator, r.operator.lower())
            serialized.append(
                {
                    "metric_name": r.metric_name,
                    "operator": op,
                    "threshold": r.threshold,
                    "is_delta": r.is_delta,
                    "severity": r.severity,
                    "enabled": r.enabled,
                }
            )
        elif isinstance(r, dict):
            d = dict(r)
            if "metric" in d and "metric_name" not in d:
                d["metric_name"] = d.pop("metric")
            if "operator" in d:
                d["operator"] = op_map.get(
                    str(d["operator"]), str(d["operator"]).lower()
                )
            d.pop("required", None)
            serialized.append(d)
    return serialized


class RegressionGatesResource(SyncResource):
    """Synchronous resource client for regression gates."""

    def create(
        self,
        name: str,
        rules: list[GateRule | dict[str, Any]],
        *,
        evaluation_id: str | UUID | None = None,
        configuration_id: str | UUID | None = None,
        description: str | None = None,
    ) -> RegressionGate:
        payload: dict[str, Any] = {
            "name": name,
            "rules": _serialize_gate_rules(rules),
        }
        if description is not None:
            payload["description"] = description
        cfg_id = configuration_id or evaluation_id
        if cfg_id is not None:
            payload["configuration_id"] = str(cfg_id)
        data = self._transport.request("POST", "regression-gates", json=payload)
        return RegressionGate.model_validate(data)

    def get(self, gate_id: str | UUID) -> RegressionGate:
        data = self._transport.request("GET", f"regression-gates/{gate_id}")
        return RegressionGate.model_validate(data)

    def list(self, page: int = 1, page_size: int = 20) -> Page[RegressionGate]:
        params = {"page": page, "page_size": page_size}
        data = self._transport.request("GET", "regression-gates", params=params)
        items = [RegressionGate.model_validate(item) for item in data.get("items", [])]
        return Page[RegressionGate](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    def update(
        self,
        gate_id: str | UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        rules: builtins.list[GateRule | dict[str, Any]] | None = None,
    ) -> RegressionGate:
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if rules is not None:
            payload["rules"] = _serialize_gate_rules(rules)
        data = self._transport.request(
            "PATCH", f"regression-gates/{gate_id}", json=payload
        )
        return RegressionGate.model_validate(data)

    def delete(self, gate_id: str | UUID) -> None:
        self._transport.request("DELETE", f"regression-gates/{gate_id}")

    def evaluate(
        self,
        gate_id: str | UUID,
        candidate_run_id: str | UUID,
        *,
        baseline_run_id: str | UUID | None = None,
    ) -> GateEvaluationResult:
        payload: dict[str, Any] = {"candidate_run_id": str(candidate_run_id)}
        if baseline_run_id:
            payload["baseline_run_id"] = str(baseline_run_id)
        data = self._transport.request(
            "POST", f"regression-gates/{gate_id}/evaluate", json=payload
        )
        return GateEvaluationResult.model_validate(data)

    def list_versions(
        self, gate_id: str | UUID
    ) -> builtins.list[RegressionGateVersion]:
        data = self._transport.request("GET", f"regression-gates/{gate_id}/versions")
        return [RegressionGateVersion.model_validate(item) for item in data]

    def get_version(self, gate_id: str | UUID, version: int) -> RegressionGateVersion:
        data = self._transport.request(
            "GET", f"regression-gates/{gate_id}/versions/{version}"
        )
        return RegressionGateVersion.model_validate(data)

    def list_evaluations(
        self,
        gate_id: str | UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[GateEvaluationResult]:
        params = {"page": page, "page_size": page_size}
        data = self._transport.request(
            "GET", f"regression-gates/{gate_id}/evaluations", params=params
        )
        items = [
            GateEvaluationResult.model_validate(item) for item in data.get("items", [])
        ]
        return Page[GateEvaluationResult](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )


class AsyncRegressionGatesResource(AsyncResource):
    """Asynchronous resource client for regression gates."""

    async def create(
        self,
        name: str,
        rules: list[GateRule | dict[str, Any]],
        *,
        evaluation_id: str | UUID | None = None,
        configuration_id: str | UUID | None = None,
        description: str | None = None,
    ) -> RegressionGate:
        payload: dict[str, Any] = {
            "name": name,
            "rules": _serialize_gate_rules(rules),
        }
        if description is not None:
            payload["description"] = description
        cfg_id = configuration_id or evaluation_id
        if cfg_id is not None:
            payload["configuration_id"] = str(cfg_id)
        data = await self._transport.request("POST", "regression-gates", json=payload)
        return RegressionGate.model_validate(data)

    async def get(self, gate_id: str | UUID) -> RegressionGate:
        data = await self._transport.request("GET", f"regression-gates/{gate_id}")
        return RegressionGate.model_validate(data)

    async def list(self, page: int = 1, page_size: int = 20) -> Page[RegressionGate]:
        params = {"page": page, "page_size": page_size}
        data = await self._transport.request("GET", "regression-gates", params=params)
        items = [RegressionGate.model_validate(item) for item in data.get("items", [])]
        return Page[RegressionGate](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    async def update(
        self,
        gate_id: str | UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        rules: builtins.list[GateRule | dict[str, Any]] | None = None,
    ) -> RegressionGate:
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if rules is not None:
            payload["rules"] = _serialize_gate_rules(rules)
        data = await self._transport.request(
            "PATCH", f"regression-gates/{gate_id}", json=payload
        )
        return RegressionGate.model_validate(data)

    async def delete(self, gate_id: str | UUID) -> None:
        await self._transport.request("DELETE", f"regression-gates/{gate_id}")

    async def evaluate(
        self,
        gate_id: str | UUID,
        candidate_run_id: str | UUID,
        *,
        baseline_run_id: str | UUID | None = None,
    ) -> GateEvaluationResult:
        payload: dict[str, Any] = {"candidate_run_id": str(candidate_run_id)}
        if baseline_run_id:
            payload["baseline_run_id"] = str(baseline_run_id)
        data = await self._transport.request(
            "POST", f"regression-gates/{gate_id}/evaluate", json=payload
        )
        return GateEvaluationResult.model_validate(data)

    async def list_versions(
        self, gate_id: str | UUID
    ) -> builtins.list[RegressionGateVersion]:
        data = await self._transport.request(
            "GET", f"regression-gates/{gate_id}/versions"
        )
        return [RegressionGateVersion.model_validate(item) for item in data]

    async def get_version(
        self, gate_id: str | UUID, version: int
    ) -> RegressionGateVersion:
        data = await self._transport.request(
            "GET", f"regression-gates/{gate_id}/versions/{version}"
        )
        return RegressionGateVersion.model_validate(data)

    async def list_evaluations(
        self,
        gate_id: str | UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[GateEvaluationResult]:
        params = {"page": page, "page_size": page_size}
        data = await self._transport.request(
            "GET", f"regression-gates/{gate_id}/evaluations", params=params
        )
        items = [
            GateEvaluationResult.model_validate(item) for item in data.get("items", [])
        ]
        return Page[GateEvaluationResult](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )
