"""Typed pagination structures and utilities for the EVALX SDK."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Represents a single paginated page of results from the EVALX API."""

    items: list[T]
    total: int = Field(ge=0, description="Total number of items matching query")
    page: int = Field(ge=1, description="Current page number")
    page_size: int = Field(ge=1, description="Number of items per page")
    total_pages: int = Field(ge=0, description="Total number of pages")

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[T]:  # type: ignore[override]
        return iter(self.items)

    def __getitem__(self, index: int) -> T:
        return self.items[index]

    @property
    def has_next_page(self) -> bool:
        """Returns True if there is a subsequent page available."""
        return self.page < self.total_pages

    @property
    def is_empty(self) -> bool:
        """Returns True if this page contains no items."""
        return len(self.items) == 0


def paginate_all(
    fetch_page_fn: Callable[[int, int], Page[T]],
    *,
    initial_page: int = 1,
    page_size: int = 50,
    max_items: int = 1000,
) -> Iterator[T]:
    """Iterates through all items across pages with a safety bound on max_items."""
    current_page = initial_page
    yielded_count = 0

    while yielded_count < max_items:
        page = fetch_page_fn(current_page, page_size)
        if not page.items:
            break

        for item in page.items:
            yield item
            yielded_count += 1
            if yielded_count >= max_items:
                return

        if not page.has_next_page:
            break

        current_page += 1


async def async_paginate_all(
    fetch_page_fn: Callable[[int, int], Awaitable[Page[T]]],
    *,
    initial_page: int = 1,
    page_size: int = 50,
    max_items: int = 1000,
) -> AsyncIterator[T]:
    """Asynchronously iterates through all items with a safety bound on max_items."""
    current_page = initial_page
    yielded_count = 0

    while yielded_count < max_items:
        page: Page[T] = await fetch_page_fn(current_page, page_size)
        if not page.items:
            break

        for item in page.items:
            yield item
            yielded_count += 1
            if yielded_count >= max_items:
                return

        if not page.has_next_page:
            break

        current_page += 1
