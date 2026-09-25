"""Unit tests for SDK pagination structures and bounded iteration."""

from __future__ import annotations

import pytest

from evalx.pagination import Page, async_paginate_all, paginate_all


def test_page_container_basics() -> None:
    page = Page[str](
        items=["item1", "item2", "item3"],
        total=10,
        page=1,
        page_size=3,
        total_pages=4,
    )
    assert len(page) == 3
    assert page[0] == "item1"
    assert list(page) == ["item1", "item2", "item3"]
    assert page.has_next_page is True
    assert page.is_empty is False

    last_page = Page[str](
        items=["item10"],
        total=10,
        page=4,
        page_size=3,
        total_pages=4,
    )
    assert last_page.has_next_page is False


def test_paginate_all_bounded_iteration() -> None:
    # 5 pages of 2 items = 10 items total
    def fetch_page(p: int, size: int) -> Page[int]:
        start = (p - 1) * size
        items = list(range(start, min(start + size, 10)))
        return Page[int](
            items=items,
            total=10,
            page=p,
            page_size=size,
            total_pages=5,
        )

    # 1. Fetch all items
    all_items = list(paginate_all(fetch_page, page_size=2, max_items=100))
    assert all_items == list(range(10))

    # 2. Strict safety cap at max_items=5
    capped_items = list(paginate_all(fetch_page, page_size=2, max_items=5))
    assert len(capped_items) == 5
    assert capped_items == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_async_paginate_all_bounded_iteration() -> None:
    async def fetch_page(p: int, size: int) -> Page[int]:
        start = (p - 1) * size
        items = list(range(start, min(start + size, 10)))
        return Page[int](
            items=items,
            total=10,
            page=p,
            page_size=size,
            total_pages=5,
        )

    items: list[int] = []
    async for item in async_paginate_all(fetch_page, page_size=3, max_items=7):
        items.append(item)

    assert len(items) == 7
    assert items == [0, 1, 2, 3, 4, 5, 6]
