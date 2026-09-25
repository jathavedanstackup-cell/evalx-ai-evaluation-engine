"""Datasets resource clients (synchronous and asynchronous)."""

from __future__ import annotations

import builtins
from typing import Any
from uuid import UUID

from evalx.models.dataset import (
    Dataset,
    DatasetCase,
    DatasetCaseBulkCreateResponse,
    DatasetCaseCreate,
    DatasetValidationResult,
)
from evalx.pagination import Page
from evalx.resources.base import AsyncResource, SyncResource


class DatasetsResource(SyncResource):
    """Synchronous resource client for datasets and dataset cases."""

    def create(self, name: str, description: str | None = None) -> Dataset:
        payload = {"name": name, "description": description}
        data = self._transport.request("POST", "datasets", json=payload)
        return Dataset.model_validate(data)

    def get(self, dataset_id: str | UUID) -> Dataset:
        data = self._transport.request("GET", f"datasets/{dataset_id}")
        return Dataset.model_validate(data)

    def list(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
    ) -> Page[Dataset]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        data = self._transport.request("GET", "datasets", params=params)
        items = [Dataset.model_validate(item) for item in data.get("items", [])]
        return Page[Dataset](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    def update(
        self,
        dataset_id: str | UUID,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Dataset:
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        data = self._transport.request("PATCH", f"datasets/{dataset_id}", json=payload)
        return Dataset.model_validate(data)

    def delete(self, dataset_id: str | UUID) -> None:
        self._transport.request("DELETE", f"datasets/{dataset_id}")

    def create_case(
        self,
        dataset_id: str | UUID,
        *,
        input: str,
        expected_output: str | None = None,
        context: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> DatasetCase:
        payload = {
            "input": input,
            "expected_output": expected_output,
            "context": context,
            "metadata": metadata,
        }
        data = self._transport.request(
            "POST", f"datasets/{dataset_id}/cases", json=payload
        )
        return DatasetCase.model_validate(data)

    def get_case(self, dataset_id: str | UUID, case_id: str | UUID) -> DatasetCase:
        data = self._transport.request("GET", f"datasets/{dataset_id}/cases/{case_id}")
        return DatasetCase.model_validate(data)

    def list_cases(
        self,
        dataset_id: str | UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[DatasetCase]:
        params = {"page": page, "page_size": page_size}
        data = self._transport.request(
            "GET", f"datasets/{dataset_id}/cases", params=params
        )
        items = [DatasetCase.model_validate(item) for item in data.get("items", [])]
        return Page[DatasetCase](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    def update_case(
        self,
        dataset_id: str | UUID,
        case_id: str | UUID,
        *,
        input: str | None = None,
        expected_output: str | None = None,
        context: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> DatasetCase:
        payload: dict[str, Any] = {}
        if input is not None:
            payload["input"] = input
        if expected_output is not None:
            payload["expected_output"] = expected_output
        if context is not None:
            payload["context"] = context
        if metadata is not None:
            payload["metadata"] = metadata
        data = self._transport.request(
            "PATCH", f"datasets/{dataset_id}/cases/{case_id}", json=payload
        )
        return DatasetCase.model_validate(data)

    def delete_case(self, dataset_id: str | UUID, case_id: str | UUID) -> None:
        self._transport.request("DELETE", f"datasets/{dataset_id}/cases/{case_id}")

    def bulk_create_cases(
        self,
        dataset_id: str | UUID,
        cases: builtins.list[DatasetCaseCreate | dict[str, Any]],
    ) -> DatasetCaseBulkCreateResponse:
        serialized = [
            c.model_dump() if isinstance(c, DatasetCaseCreate) else c for c in cases
        ]
        payload = {"cases": serialized}
        data = self._transport.request(
            "POST", f"datasets/{dataset_id}/cases/bulk", json=payload
        )
        return DatasetCaseBulkCreateResponse.model_validate(data)

    def validate(self, dataset_id: str | UUID) -> DatasetValidationResult:
        data = self._transport.request("POST", f"datasets/{dataset_id}/validate")
        return DatasetValidationResult.model_validate(data)


class AsyncDatasetsResource(AsyncResource):
    """Asynchronous resource client for datasets and dataset cases."""

    async def create(self, name: str, description: str | None = None) -> Dataset:
        payload = {"name": name, "description": description}
        data = await self._transport.request("POST", "datasets", json=payload)
        return Dataset.model_validate(data)

    async def get(self, dataset_id: str | UUID) -> Dataset:
        data = await self._transport.request("GET", f"datasets/{dataset_id}")
        return Dataset.model_validate(data)

    async def list(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
    ) -> Page[Dataset]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        data = await self._transport.request("GET", "datasets", params=params)
        items = [Dataset.model_validate(item) for item in data.get("items", [])]
        return Page[Dataset](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    async def update(
        self,
        dataset_id: str | UUID,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Dataset:
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        data = await self._transport.request(
            "PATCH", f"datasets/{dataset_id}", json=payload
        )
        return Dataset.model_validate(data)

    async def delete(self, dataset_id: str | UUID) -> None:
        await self._transport.request("DELETE", f"datasets/{dataset_id}")

    async def create_case(
        self,
        dataset_id: str | UUID,
        *,
        input: str,
        expected_output: str | None = None,
        context: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> DatasetCase:
        payload = {
            "input": input,
            "expected_output": expected_output,
            "context": context,
            "metadata": metadata,
        }
        data = await self._transport.request(
            "POST", f"datasets/{dataset_id}/cases", json=payload
        )
        return DatasetCase.model_validate(data)

    async def get_case(
        self, dataset_id: str | UUID, case_id: str | UUID
    ) -> DatasetCase:
        data = await self._transport.request(
            "GET", f"datasets/{dataset_id}/cases/{case_id}"
        )
        return DatasetCase.model_validate(data)

    async def list_cases(
        self,
        dataset_id: str | UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[DatasetCase]:
        params = {"page": page, "page_size": page_size}
        data = await self._transport.request(
            "GET", f"datasets/{dataset_id}/cases", params=params
        )
        items = [DatasetCase.model_validate(item) for item in data.get("items", [])]
        return Page[DatasetCase](
            items=items,
            total=data.get("total", len(items)),
            page=data.get("page", page),
            page_size=data.get("page_size", page_size),
            total_pages=data.get("total_pages", 1),
        )

    async def update_case(
        self,
        dataset_id: str | UUID,
        case_id: str | UUID,
        *,
        input: str | None = None,
        expected_output: str | None = None,
        context: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> DatasetCase:
        payload: dict[str, Any] = {}
        if input is not None:
            payload["input"] = input
        if expected_output is not None:
            payload["expected_output"] = expected_output
        if context is not None:
            payload["context"] = context
        if metadata is not None:
            payload["metadata"] = metadata
        data = await self._transport.request(
            "PATCH", f"datasets/{dataset_id}/cases/{case_id}", json=payload
        )
        return DatasetCase.model_validate(data)

    async def delete_case(self, dataset_id: str | UUID, case_id: str | UUID) -> None:
        await self._transport.request(
            "DELETE", f"datasets/{dataset_id}/cases/{case_id}"
        )

    async def bulk_create_cases(
        self,
        dataset_id: str | UUID,
        cases: builtins.list[DatasetCaseCreate | dict[str, Any]],
    ) -> DatasetCaseBulkCreateResponse:
        serialized = [
            c.model_dump() if isinstance(c, DatasetCaseCreate) else c for c in cases
        ]
        payload = {"cases": serialized}
        data = await self._transport.request(
            "POST", f"datasets/{dataset_id}/cases/bulk", json=payload
        )
        return DatasetCaseBulkCreateResponse.model_validate(data)

    async def validate(self, dataset_id: str | UUID) -> DatasetValidationResult:
        data = await self._transport.request("POST", f"datasets/{dataset_id}/validate")
        return DatasetValidationResult.model_validate(data)
