"""Unit tests for bounded evaluation run polling (wait_for_completion)."""

from __future__ import annotations

import uuid

import httpx
import pytest

from evalx.client import AsyncEvalXClient, EvalXClient
from evalx.config import EvalXConfig
from evalx.exceptions import EvalXTimeoutError


def test_sync_wait_for_completion_success() -> None:
    run_id = str(uuid.uuid4())
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        polls += 1
        status = "RUNNING" if polls < 3 else "COMPLETED"
        return httpx.Response(
            200,
            json={
                "id": run_id,
                "evaluation_id": str(uuid.uuid4()),
                "status": status,
                "aggregate_score": 0.92,
                "created_at": "2026-09-22T12:00:00Z",
                "updated_at": "2026-09-22T12:00:00Z",
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = EvalXConfig(base_url="http://testserver")
    client = EvalXClient(config=cfg, http_client=mock_client)

    run = client.evaluations.runs.wait_for_completion(
        run_id,
        timeout_seconds=5.0,
        poll_interval_seconds=0.01,
    )
    assert run.status == "COMPLETED"
    assert run.aggregate_score == 0.92
    assert polls == 3


def test_sync_wait_for_completion_timeout() -> None:
    run_id = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": run_id,
                "evaluation_id": str(uuid.uuid4()),
                "status": "RUNNING",
                "created_at": "2026-09-22T12:00:00Z",
                "updated_at": "2026-09-22T12:00:00Z",
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = EvalXConfig(base_url="http://testserver")
    client = EvalXClient(config=cfg, http_client=mock_client)

    with pytest.raises(EvalXTimeoutError) as exc_info:
        client.evaluations.runs.wait_for_completion(
            run_id,
            timeout_seconds=0.05,
            poll_interval_seconds=0.01,
        )
    assert "Timed out waiting for evaluation run" in str(exc_info.value)


@pytest.mark.asyncio
async def test_async_wait_for_completion_success() -> None:
    run_id = str(uuid.uuid4())
    polls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        polls += 1
        status = "RUNNING" if polls < 2 else "FAILED"
        return httpx.Response(
            200,
            json={
                "id": run_id,
                "evaluation_id": str(uuid.uuid4()),
                "status": status,
                "error_message": "Provider rate limit exhausted",
                "created_at": "2026-09-22T12:00:00Z",
                "updated_at": "2026-09-22T12:00:00Z",
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = EvalXConfig(base_url="http://testserver")
    client = AsyncEvalXClient(config=cfg, http_client=mock_client)

    run = await client.evaluations.runs.wait_for_completion(
        run_id,
        timeout_seconds=5.0,
        poll_interval_seconds=0.01,
    )
    assert run.status == "FAILED"
    assert run.error_message == "Provider rate limit exhausted"
    assert polls == 2
