"""Base resource classes for sync and async SDK clients."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evalx.transport import AsyncHttpTransport, HttpTransport


class SyncResource:
    """Base class for synchronous resource clients."""

    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport


class AsyncResource:
    """Base class for asynchronous resource clients."""

    def __init__(self, transport: AsyncHttpTransport) -> None:
        self._transport = transport
