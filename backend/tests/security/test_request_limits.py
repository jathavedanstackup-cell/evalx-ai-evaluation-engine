from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.test_provider import make_test_auth_headers
from app.core.config import settings
from app.main import app
from tests.test_datasets_api import managed_api_client


@pytest.mark.asyncio
async def test_content_length_exceeding_max_returns_413() -> None:
    """Content-Length exceeding maximum limit must immediately return 413."""
    transport = ASGITransport(app=app)
    # Configure 10 KB limit temporarily
    original_limit = settings.max_request_body_bytes
    settings.max_request_body_bytes = 10_000

    try:
        oversized_payload = b"x" * 15_000
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            headers = {
                "Content-Length": str(len(oversized_payload)),
                "Content-Type": "application/json",
            }
            response = await client.post(
                "/api/v1/datasets",
                content=oversized_payload,
                headers=headers,
            )

        assert response.status_code == 413
        data = response.json()
        assert data["error_code"] == "PAYLOAD_TOO_LARGE"
        assert "maximum allowable limit" in data["detail"]
        assert "x-correlation-id" in response.headers
    finally:
        settings.max_request_body_bytes = original_limit


@pytest.mark.asyncio
async def test_chunked_streaming_request_valid_and_exceeded() -> None:
    """Valid chunked requests succeed; oversized chunked streams return 413."""
    auth_headers = make_test_auth_headers("stream-user")

    # 1. Valid chunked request (no Content-Length)
    async def valid_chunks() -> AsyncGenerator[bytes]:
        yield b'{"name": "Valid '
        yield b'Chunked Dataset"}'

    async with managed_api_client() as client:
        req_headers = {
            **auth_headers,
            "Content-Type": "application/json",
        }
        res_valid = await client.post(
            "/api/v1/datasets",
            content=valid_chunks(),
            headers=req_headers,
        )

        assert res_valid.status_code == 201
        assert res_valid.json()["name"] == "Valid Chunked Dataset"

        # 2. Oversized chunked request (exceeds bounded receive)
        original_limit = settings.max_request_body_bytes
        settings.max_request_body_bytes = 10_000

        try:

            async def oversized_chunks() -> AsyncGenerator[bytes]:
                for _ in range(15):
                    yield b"z" * 1024  # 15 KB total > 10 KB limit

            res_oversized = await client.post(
                "/api/v1/datasets",
                content=oversized_chunks(),
                headers={
                    **auth_headers,
                    "Content-Type": "application/json",
                },
            )

            assert res_oversized.status_code == 413
            assert res_oversized.json()["error_code"] == "PAYLOAD_TOO_LARGE"
        finally:
            settings.max_request_body_bytes = original_limit


@pytest.mark.asyncio
async def test_bounded_pagination_policy() -> None:
    """Querying pages > 10,000 or page_size > 100 must be rejected with 422."""
    auth_headers = make_test_auth_headers("pagination-user")

    async with managed_api_client() as client:
        # page > 10,000
        res_page = await client.get(
            "/api/v1/datasets?page=10001",
            headers=auth_headers,
        )
        assert res_page.status_code == 422

        # page_size > 100
        res_size = await client.get(
            "/api/v1/datasets?page_size=101",
            headers=auth_headers,
        )
        assert res_size.status_code == 422

        # page < 1
        res_zero = await client.get(
            "/api/v1/datasets?page=0",
            headers=auth_headers,
        )
        assert res_zero.status_code == 422


@pytest.mark.asyncio
async def test_search_query_length_bound() -> None:
    """Search query parameter exceeding 100 characters must return 422."""
    auth_headers = make_test_auth_headers("search-user")
    oversized_search = "s" * 101

    async with managed_api_client() as client:
        res = await client.get(
            f"/api/v1/datasets?search={oversized_search}",
            headers=auth_headers,
        )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_dataset_case_input_and_output_bounds() -> None:
    """Test cases with input/output exceeding 50,000 characters must return 422."""
    auth_headers = make_test_auth_headers("bounds-user")

    async with managed_api_client() as client:
        # Create dataset
        ds_res = await client.post(
            "/api/v1/datasets",
            json={"name": "Bounds DS"},
            headers=auth_headers,
        )
        ds_id = ds_res.json()["id"]

        # 1. Input exceeding 50,000 chars
        res_input = await client.post(
            f"/api/v1/datasets/{ds_id}/cases",
            json={"input": "a" * 50_001},
            headers=auth_headers,
        )
        assert res_input.status_code == 422

        # 2. Expected output exceeding 50,000 chars
        res_output = await client.post(
            f"/api/v1/datasets/{ds_id}/cases",
            json={"input": "Valid", "expected_output": "b" * 50_001},
            headers=auth_headers,
        )
        assert res_output.status_code == 422

        # 3. Context exceeding 50 passages
        passages = ["passage"] * 51
        res_ctx = await client.post(
            f"/api/v1/datasets/{ds_id}/cases",
            json={"input": "Valid", "context": passages},
            headers=auth_headers,
        )
        assert res_ctx.status_code == 422

        # 4. Metadata exceeding 50 keys
        meta = {f"k_{i}": i for i in range(51)}
        res_meta = await client.post(
            f"/api/v1/datasets/{ds_id}/cases",
            json={"input": "Valid", "metadata": meta},
            headers=auth_headers,
        )
        assert res_meta.status_code == 422
