"""Evaluation configurations resource clients (synchronous and asynchronous)."""

from __future__ import annotations

import builtins
from typing import Any
from uuid import UUID

from evalx.models.configuration import (
    EvaluationConfig,
    EvaluationConfigVersion,
    EvaluatorSpec,
)
from evalx.pagination import Page
from evalx.resources.base import AsyncResource, SyncResource


def _serialize_evaluators(
    evaluators: list[EvaluatorSpec | dict[str, Any]],
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for e in evaluators:
        if isinstance(e, EvaluatorSpec):
            d: dict[str, Any] = {
                "evaluator_type": e.evaluator_type,
                "backend": e.backend,
                "weight": e.weight,
                "enabled": e.enabled,
            }
            if e.threshold is not None:
                d["threshold"] = e.threshold
            if e.name is not None:
                d["name"] = e.name
            if e.configuration is not None:
                d["configuration"] = e.configuration
            specs.append(d)
        elif isinstance(e, dict):
            specs.append(e)
    return specs


class ConfigurationsResource(SyncResource):
    """Synchronous resource client for evaluation configurations."""

    def create(
        self,
        name: str,
        evaluators: list[EvaluatorSpec | dict[str, Any]],
        *,
        description: str | None = None,
        preset_name: str | None = None,
    ) -> EvaluationConfig:
        payload: dict[str, Any] = {
            "name": name,
            "evaluators": _serialize_evaluators(evaluators),
        }
        if description is not None:
            payload["description"] = description
        if preset_name is not None:
            payload["preset_name"] = preset_name
        data = self._transport.request("POST", "evaluation-configs", json=payload)
        return EvaluationConfig.model_validate(data)

    def get(
        self,
        config_id: str | UUID,
        *,
        version: int | None = None,
    ) -> EvaluationConfig | EvaluationConfigVersion:
        params = {"version": version} if version is not None else None
        data = self._transport.request(
            "GET", f"evaluation-configs/{config_id}", params=params
        )
        if version is not None:
            return EvaluationConfigVersion.model_validate(data)
        return EvaluationConfig.model_validate(data)

    def list(self, page: int = 1, page_size: int = 20) -> Page[EvaluationConfig]:
        params = {"page": page, "page_size": page_size}
        data = self._transport.request("GET", "evaluation-configs", params=params)
        items = [
            EvaluationConfig.model_validate(item) for item in data.get("items", [])
        ]
        return Page[EvaluationConfig](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    def update(
        self,
        config_id: str | UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        preset_name: str | None = None,
        evaluators: builtins.list[EvaluatorSpec | dict[str, Any]] | None = None,
    ) -> EvaluationConfig:
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if preset_name is not None:
            payload["preset_name"] = preset_name
        if evaluators is not None:
            payload["evaluators"] = [
                e.model_dump() if isinstance(e, EvaluatorSpec) else e
                for e in evaluators
            ]
            payload["evaluators"] = _serialize_evaluators(evaluators)
        data = self._transport.request(
            "PATCH", f"evaluation-configs/{config_id}", json=payload
        )
        return EvaluationConfig.model_validate(data)

    def delete(self, config_id: str | UUID) -> None:
        self._transport.request("DELETE", f"evaluation-configs/{config_id}")


class AsyncConfigurationsResource(AsyncResource):
    """Asynchronous resource client for evaluation configurations."""

    async def create(
        self,
        name: str,
        evaluators: list[EvaluatorSpec | dict[str, Any]],
        *,
        description: str | None = None,
        preset_name: str | None = None,
    ) -> EvaluationConfig:
        payload: dict[str, Any] = {
            "name": name,
            "evaluators": _serialize_evaluators(evaluators),
        }
        if description is not None:
            payload["description"] = description
        if preset_name is not None:
            payload["preset_name"] = preset_name
        data = await self._transport.request("POST", "evaluation-configs", json=payload)
        return EvaluationConfig.model_validate(data)

    async def get(
        self,
        config_id: str | UUID,
        *,
        version: int | None = None,
    ) -> EvaluationConfig | EvaluationConfigVersion:
        params = {"version": version} if version is not None else None
        data = await self._transport.request(
            "GET", f"evaluation-configs/{config_id}", params=params
        )
        if version is not None:
            return EvaluationConfigVersion.model_validate(data)
        return EvaluationConfig.model_validate(data)

    async def list(self, page: int = 1, page_size: int = 20) -> Page[EvaluationConfig]:
        params = {"page": page, "page_size": page_size}
        data = await self._transport.request("GET", "evaluation-configs", params=params)
        data = await self._transport.request("GET", "evaluation-configs", params=params)
        items = [
            EvaluationConfig.model_validate(item) for item in data.get("items", [])
        ]
        return Page[EvaluationConfig](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    async def update(
        self,
        config_id: str | UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        preset_name: str | None = None,
        evaluators: builtins.list[EvaluatorSpec | dict[str, Any]] | None = None,
    ) -> EvaluationConfig:
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if preset_name is not None:
            payload["preset_name"] = preset_name
        if evaluators is not None:
            payload["evaluators"] = [
                e.model_dump() if isinstance(e, EvaluatorSpec) else e
                for e in evaluators
            ]
            payload["evaluators"] = _serialize_evaluators(evaluators)
        data = await self._transport.request(
            "PATCH", f"evaluation-configs/{config_id}", json=payload
        )
        return EvaluationConfig.model_validate(data)

    async def delete(self, config_id: str | UUID) -> None:
        await self._transport.request("DELETE", f"evaluation-configs/{config_id}")
