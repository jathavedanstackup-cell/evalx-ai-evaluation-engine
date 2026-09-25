"""Audit and metrics resource clients (synchronous and asynchronous)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from evalx.models.audit import AuditEvent, MetricsSnapshot
from evalx.pagination import Page
from evalx.resources.base import AsyncResource, SyncResource


class AuditResource(SyncResource):
    """Synchronous resource client for tenant audit events and metrics."""

    def list(
        self,
        *,
        action: str | None = None,
        event_type: str | None = None,
        resource_type: str | None = None,
        resource_id: str | UUID | None = None,
        correlation_id: str | None = None,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Page[AuditEvent]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if action:
            params["action"] = action
        event_kind = event_type or action
        if event_kind:
            params["event_type"] = event_kind
        if resource_type:
            params["resource_type"] = resource_type
        if resource_id:
            params["resource_id"] = str(resource_id)
        if correlation_id:
            params["correlation_id"] = correlation_id
        if from_time:
            params["from_time"] = from_time.isoformat()
            params["from_timestamp"] = from_time.isoformat()
        if to_time:
            params["to_time"] = to_time.isoformat()
            params["to_timestamp"] = to_time.isoformat()

        data = self._transport.request("GET", "audit-events", params=params)
        items = [AuditEvent.model_validate(item) for item in data.get("items", [])]
        return Page[AuditEvent](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    def get_metrics(self) -> MetricsSnapshot:
        data = self._transport.request("GET", "metrics")
        return MetricsSnapshot.model_validate(data)


class AsyncAuditResource(AsyncResource):
    """Asynchronous resource client for tenant audit events and metrics."""

    async def list(
        self,
        *,
        action: str | None = None,
        event_type: str | None = None,
        resource_type: str | None = None,
        resource_id: str | UUID | None = None,
        correlation_id: str | None = None,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Page[AuditEvent]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if action:
            params["action"] = action
        event_kind = event_type or action
        if event_kind:
            params["event_type"] = event_kind
        if resource_type:
            params["resource_type"] = resource_type
        if resource_id:
            params["resource_id"] = str(resource_id)
        if correlation_id:
            params["correlation_id"] = correlation_id
        if from_time:
            params["from_time"] = from_time.isoformat()
            params["from_timestamp"] = from_time.isoformat()
        if to_time:
            params["to_time"] = to_time.isoformat()
            params["to_timestamp"] = to_time.isoformat()

        data = await self._transport.request("GET", "audit-events", params=params)
        items = [AuditEvent.model_validate(item) for item in data.get("items", [])]
        return Page[AuditEvent](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    async def get_metrics(self) -> MetricsSnapshot:
        data = await self._transport.request("GET", "metrics")
        return MetricsSnapshot.model_validate(data)
