import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import require_test_database_url, settings
from app.database.session import get_async_session
from app.main import app
from app.models.dataset import DatasetCase


@asynccontextmanager
async def managed_api_client() -> AsyncIterator[AsyncClient]:
    """Provides an AsyncClient connected exclusively to the TEST database."""
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def get_test_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    from app.auth.dependencies import get_auth_provider
    from app.auth.test_provider import TestAuthProvider

    app.dependency_overrides[get_async_session] = get_test_db_session
    app.dependency_overrides[get_auth_provider] = lambda: TestAuthProvider(
        default_user_id="test-user-default"
    )
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(
                text(
                    "TRUNCATE TABLE users, datasets, dataset_cases, "
                    "evaluations, evaluator_configs, evaluation_runs, "
                    "evaluation_results CASCADE"
                )
            )
            await cleanup_session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_dataset_success() -> None:
    async with managed_api_client() as client:
        payload = {
            "name": "RAG Evaluation Benchmark",
            "description": "Ground truth Q&A pairs for QA system testing",
        }
        response = await client.post("/api/v1/datasets", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "RAG Evaluation Benchmark"
        assert data["description"] == "Ground truth Q&A pairs for QA system testing"
        assert data["version"] == 1
        assert data["case_count"] == 0
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data


@pytest.mark.asyncio
async def test_get_dataset_by_id() -> None:
    async with managed_api_client() as client:
        create_res = await client.post(
            "/api/v1/datasets", json={"name": "Retrieval Benchmark"}
        )
        dataset_id = create_res.json()["id"]

        get_res = await client.get(f"/api/v1/datasets/{dataset_id}")
        assert get_res.status_code == 200
        data = get_res.json()
        assert data["id"] == dataset_id
        assert data["name"] == "Retrieval Benchmark"
        assert data["case_count"] == 0
        assert data["version"] == 1


@pytest.mark.asyncio
async def test_get_dataset_not_found() -> None:
    async with managed_api_client() as client:
        non_existent_id = uuid.uuid4()
        response = await client.get(f"/api/v1/datasets/{non_existent_id}")
        assert response.status_code == 404
        assert response.json() == {"detail": "Dataset not found"}


@pytest.mark.asyncio
async def test_update_dataset_success() -> None:
    async with managed_api_client() as client:
        create_res = await client.post(
            "/api/v1/datasets", json={"name": "Initial Name", "description": "Init"}
        )
        dataset_id = create_res.json()["id"]

        patch_res = await client.patch(
            f"/api/v1/datasets/{dataset_id}",
            json={"name": "Updated Name", "description": "Updated Description"},
        )
        assert patch_res.status_code == 200
        data = patch_res.json()
        assert data["name"] == "Updated Name"
        assert data["description"] == "Updated Description"

        # Verify persistence on subsequent GET
        get_res = await client.get(f"/api/v1/datasets/{dataset_id}")
        assert get_res.status_code == 200
        assert get_res.json()["name"] == "Updated Name"


@pytest.mark.asyncio
async def test_update_dataset_not_found() -> None:
    async with managed_api_client() as client:
        non_existent_id = uuid.uuid4()
        response = await client.patch(
            f"/api/v1/datasets/{non_existent_id}", json={"name": "New"}
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Dataset not found"}


@pytest.mark.asyncio
async def test_delete_dataset_success() -> None:
    async with managed_api_client() as client:
        create_res = await client.post(
            "/api/v1/datasets", json={"name": "To Be Deleted"}
        )
        dataset_id = create_res.json()["id"]

        delete_res = await client.delete(f"/api/v1/datasets/{dataset_id}")
        assert delete_res.status_code == 204

        get_res = await client.get(f"/api/v1/datasets/{dataset_id}")
        assert get_res.status_code == 404


@pytest.mark.asyncio
async def test_delete_dataset_cascades_cases() -> None:
    async with managed_api_client() as client:
        create_res = await client.post(
            "/api/v1/datasets", json={"name": "Parent Dataset"}
        )
        dataset_id = create_res.json()["id"]

        # Add two cases
        case1_res = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"input": "Query 1", "expected_output": "Answer 1"},
        )
        case2_res = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"input": "Query 2", "expected_output": "Answer 2"},
        )
        assert case1_res.status_code == 201
        assert case2_res.status_code == 201
        case1_id = case1_res.json()["id"]

        # Delete parent dataset
        delete_res = await client.delete(f"/api/v1/datasets/{dataset_id}")
        assert delete_res.status_code == 204

        # Verify dataset is gone
        assert (await client.get(f"/api/v1/datasets/{dataset_id}")).status_code == 404

        # Verify cases endpoint for dataset returns 404
        assert (
            await client.get(f"/api/v1/datasets/{dataset_id}/cases")
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/datasets/{dataset_id}/cases/{case1_id}")
        ).status_code == 404


@pytest.mark.asyncio
async def test_delete_dataset_not_found() -> None:
    async with managed_api_client() as client:
        non_existent_id = uuid.uuid4()
        response = await client.delete(f"/api/v1/datasets/{non_existent_id}")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_datasets_pagination() -> None:
    async with managed_api_client() as client:
        for i in range(5):
            await client.post("/api/v1/datasets", json={"name": f"Benchmark Batch {i}"})

        # Page 1, size 2
        res1 = await client.get("/api/v1/datasets?page=1&page_size=2")
        assert res1.status_code == 200
        d1 = res1.json()
        assert d1["total"] == 5
        assert d1["page"] == 1
        assert d1["page_size"] == 2
        assert d1["total_pages"] == 3
        assert len(d1["items"]) == 2

        # Page 3, size 2 (should have 1 item)
        res3 = await client.get("/api/v1/datasets?page=3&page_size=2")
        assert res3.status_code == 200
        d3 = res3.json()
        assert d3["total"] == 5
        assert d3["page"] == 3
        assert d3["total_pages"] == 3
        assert len(d3["items"]) == 1


@pytest.mark.asyncio
async def test_list_datasets_search() -> None:
    async with managed_api_client() as client:
        await client.post("/api/v1/datasets", json={"name": "Math Reasoning Eval"})
        await client.post("/api/v1/datasets", json={"name": "Code Synthesis Eval"})
        await client.post("/api/v1/datasets", json={"name": "Advanced Math Olympics"})

        # Substring search (case-insensitive)
        search_res = await client.get("/api/v1/datasets?search=math")
        assert search_res.status_code == 200
        data = search_res.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2
        names = [item["name"] for item in data["items"]]
        assert "Math Reasoning Eval" in names
        assert "Advanced Math Olympics" in names
        assert "Code Synthesis Eval" not in names


@pytest.mark.asyncio
async def test_create_dataset_case_success() -> None:
    async with managed_api_client() as client:
        ds_res = await client.post(
            "/api/v1/datasets", json={"name": "Knowledge Benchmark"}
        )
        dataset_id = ds_res.json()["id"]

        case_payload = {
            "input": "What is the boiling point of water at sea level?",
            "expected_output": "100 degrees Celsius or 212 degrees Fahrenheit.",
            "context": ["Water boils at 100°C (212°F) under 1 atm of pressure."],
            "metadata": {"subject": "physics", "difficulty": "easy"},
        }
        case_res = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases", json=case_payload
        )
        assert case_res.status_code == 201
        data = case_res.json()
        assert data["input"] == case_payload["input"]
        assert data["expected_output"] == case_payload["expected_output"]
        assert data["context"] == case_payload["context"]
        assert data["metadata"] == case_payload["metadata"]
        assert data["dataset_id"] == dataset_id
        assert "id" in data

        # Verify dataset case_count was incremented
        get_ds = await client.get(f"/api/v1/datasets/{dataset_id}")
        assert get_ds.json()["case_count"] == 1


@pytest.mark.asyncio
async def test_get_dataset_case_by_id() -> None:
    async with managed_api_client() as client:
        ds_res = await client.post("/api/v1/datasets", json={"name": "DS"})
        dataset_id = ds_res.json()["id"]

        create_case = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"input": "What is 2+2?", "expected_output": "4"},
        )
        case_id = create_case.json()["id"]

        get_res = await client.get(f"/api/v1/datasets/{dataset_id}/cases/{case_id}")
        assert get_res.status_code == 200
        assert get_res.json()["id"] == case_id
        assert get_res.json()["input"] == "What is 2+2?"


@pytest.mark.asyncio
async def test_get_dataset_case_not_found() -> None:
    async with managed_api_client() as client:
        ds_res = await client.post("/api/v1/datasets", json={"name": "DS"})
        dataset_id = ds_res.json()["id"]
        random_case_id = uuid.uuid4()
        random_ds_id = uuid.uuid4()

        # Non-existent case in real dataset
        res1 = await client.get(f"/api/v1/datasets/{dataset_id}/cases/{random_case_id}")
        assert res1.status_code == 404

        # Non-existent dataset
        res2 = await client.get(
            f"/api/v1/datasets/{random_ds_id}/cases/{random_case_id}"
        )
        assert res2.status_code == 404


@pytest.mark.asyncio
async def test_update_dataset_case_success() -> None:
    async with managed_api_client() as client:
        ds_res = await client.post("/api/v1/datasets", json={"name": "DS"})
        dataset_id = ds_res.json()["id"]

        create_case = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"input": "Initial Prompt", "expected_output": "Initial Answer"},
        )
        case_id = create_case.json()["id"]

        patch_res = await client.patch(
            f"/api/v1/datasets/{dataset_id}/cases/{case_id}",
            json={
                "input": "Updated Prompt",
                "expected_output": "Updated Answer",
                "context": ["New context"],
                "metadata": {"updated": True},
            },
        )
        assert patch_res.status_code == 200
        data = patch_res.json()
        assert data["input"] == "Updated Prompt"
        assert data["expected_output"] == "Updated Answer"
        assert data["context"] == ["New context"]
        assert data["metadata"] == {"updated": True}


@pytest.mark.asyncio
async def test_delete_dataset_case_success() -> None:
    async with managed_api_client() as client:
        ds_res = await client.post("/api/v1/datasets", json={"name": "DS"})
        dataset_id = ds_res.json()["id"]

        create_case = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases", json={"input": "To delete"}
        )
        case_id = create_case.json()["id"]

        delete_res = await client.delete(
            f"/api/v1/datasets/{dataset_id}/cases/{case_id}"
        )
        assert delete_res.status_code == 204

        # Subsequent GET returns 404
        get_res = await client.get(f"/api/v1/datasets/{dataset_id}/cases/{case_id}")
        assert get_res.status_code == 404

        # Dataset case_count returns to 0
        get_ds = await client.get(f"/api/v1/datasets/{dataset_id}")
        assert get_ds.json()["case_count"] == 0


@pytest.mark.asyncio
async def test_list_dataset_cases_pagination() -> None:
    async with managed_api_client() as client:
        ds_res = await client.post("/api/v1/datasets", json={"name": "Pagination DS"})
        dataset_id = ds_res.json()["id"]

        for i in range(5):
            await client.post(
                f"/api/v1/datasets/{dataset_id}/cases",
                json={"input": f"Case Prompt {i}"},
            )

        res = await client.get(
            f"/api/v1/datasets/{dataset_id}/cases?page=1&page_size=3"
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 5
        assert data["page"] == 1
        assert data["page_size"] == 3
        assert data["total_pages"] == 2
        assert len(data["items"]) == 3


@pytest.mark.asyncio
async def test_case_validation_missing_or_blank_input() -> None:
    async with managed_api_client() as client:
        ds_res = await client.post("/api/v1/datasets", json={"name": "Val DS"})
        dataset_id = ds_res.json()["id"]

        # Missing input
        res1 = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"expected_output": "Answer"},
        )
        assert res1.status_code == 422

        # Whitespace-only input
        res2 = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"input": "   "},
        )
        assert res2.status_code == 422

        # Whitespace-only dataset name
        res3 = await client.post(
            "/api/v1/datasets",
            json={"name": "   "},
        )
        assert res3.status_code == 422


@pytest.mark.asyncio
async def test_bulk_create_cases_success() -> None:
    async with managed_api_client() as client:
        ds_res = await client.post("/api/v1/datasets", json={"name": "Bulk DS"})
        dataset_id = ds_res.json()["id"]

        payload = {
            "cases": [
                {
                    "input": f"Bulk question {i}",
                    "expected_output": f"Bulk answer {i}",
                    "context": [f"Reference passage {i}"],
                    "metadata": {"batch_id": 1, "item_idx": i},
                }
                for i in range(10)
            ]
        }
        bulk_res = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases/bulk", json=payload
        )
        assert bulk_res.status_code == 201
        data = bulk_res.json()
        assert data["dataset_id"] == dataset_id
        assert data["inserted_count"] == 10
        assert len(data["cases"]) == 10

        # Verify case count on dataset
        get_ds = await client.get(f"/api/v1/datasets/{dataset_id}")
        assert get_ds.json()["case_count"] == 10


@pytest.mark.asyncio
async def test_bulk_create_cases_validation_bounds() -> None:
    async with managed_api_client() as client:
        ds_res = await client.post("/api/v1/datasets", json={"name": "Bounds DS"})
        dataset_id = ds_res.json()["id"]

        # Empty cases array fails validation
        empty_res = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases/bulk",
            json={"cases": []},
        )
        assert empty_res.status_code == 422

        # Missing cases key fails validation
        missing_res = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases/bulk",
            json={},
        )
        assert missing_res.status_code == 422


@pytest.mark.asyncio
async def test_bulk_create_cases_atomic_rollback_on_validation_failure() -> None:
    async with managed_api_client() as client:
        ds_res = await client.post("/api/v1/datasets", json={"name": "Atomic DS"})
        dataset_id = ds_res.json()["id"]

        # 2 valid cases and 1 invalid case with whitespace input
        payload = {
            "cases": [
                {"input": "Valid 1", "expected_output": "Answer 1"},
                {"input": "   ", "expected_output": "Invalid Answer"},
                {"input": "Valid 2", "expected_output": "Answer 2"},
            ]
        }
        res = await client.post(
            f"/api/v1/datasets/{dataset_id}/cases/bulk", json=payload
        )
        assert res.status_code == 422

        # Verify zero cases were inserted (atomic rejection)
        get_ds = await client.get(f"/api/v1/datasets/{dataset_id}")
        assert get_ds.json()["case_count"] == 0

        list_cases = await client.get(f"/api/v1/datasets/{dataset_id}/cases")
        assert list_cases.json()["total"] == 0


@pytest.mark.asyncio
async def test_bulk_create_nonexistent_dataset_404() -> None:
    async with managed_api_client() as client:
        non_existent_id = uuid.uuid4()
        payload = {"cases": [{"input": "Sample", "expected_output": "Output"}]}
        res = await client.post(
            f"/api/v1/datasets/{non_existent_id}/cases/bulk", json=payload
        )
        assert res.status_code == 404
        assert res.json() == {"detail": "Dataset not found"}


@pytest.mark.asyncio
async def test_validate_dataset_all_valid() -> None:
    async with managed_api_client() as client:
        ds_res = await client.post("/api/v1/datasets", json={"name": "Valid DS"})
        dataset_id = ds_res.json()["id"]

        # Insert 3 high-quality cases
        await client.post(
            f"/api/v1/datasets/{dataset_id}/cases/bulk",
            json={
                "cases": [
                    {
                        "input": f"Distinct question {i}?",
                        "expected_output": f"Distinct answer {i}.",
                        "context": [f"Verified context passage {i}."],
                        "metadata": {"category": "test"},
                    }
                    for i in range(3)
                ]
            },
        )

        val_res = await client.post(f"/api/v1/datasets/{dataset_id}/validate")
        assert val_res.status_code == 200
        data = val_res.json()
        assert data["is_valid"] is True
        assert data["total_cases"] == 3
        assert data["error_count"] == 0
        assert data["warning_count"] == 0
        assert len(data["issues"]) == 0
        assert data["summary"]["cases_with_expected_output"] == 3
        assert data["summary"]["cases_with_context"] == 3
        assert data["summary"]["cases_with_metadata"] == 3


@pytest.mark.asyncio
async def test_validate_dataset_duplicate_inputs_reports_warning() -> None:
    async with managed_api_client() as client:
        ds_res = await client.post("/api/v1/datasets", json={"name": "Duplicate DS"})
        dataset_id = ds_res.json()["id"]

        await client.post(
            f"/api/v1/datasets/{dataset_id}/cases/bulk",
            json={
                "cases": [
                    {
                        "input": "What is artificial intelligence?",
                        "expected_output": "AI",
                    },
                    {
                        "input": "  what is artificial intelligence?  ",
                        "expected_output": "AI definition 2",
                    },
                ]
            },
        )

        val_res = await client.post(f"/api/v1/datasets/{dataset_id}/validate")
        assert val_res.status_code == 200
        data = val_res.json()
        # Warning does not invalidate the dataset
        assert data["is_valid"] is True
        assert data["error_count"] == 0
        assert data["warning_count"] == 1
        assert len(data["issues"]) == 1
        assert data["issues"][0]["severity"] == "warning"
        assert data["issues"][0]["field"] == "input"
        assert "Duplicate input detected" in data["issues"][0]["message"]


@pytest.mark.asyncio
async def test_validate_dataset_malformed_context_reports_error() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with managed_api_client() as client:
        # Create dataset through authenticated client
        create_res = await client.post(
            "/api/v1/datasets", json={"name": "Malformed Context DS"}
        )
        assert create_res.status_code == 201
        ds_id = create_res.json()["id"]

        async with session_factory() as session:
            # Insert a case directly with an empty string in context list
            case = DatasetCase(
                dataset_id=uuid.UUID(ds_id),
                input="Valid query?",
                expected_output="Output",
                context=[""],  # Empty chunk triggers validation error
                metadata_={},
            )
            session.add(case)
            await session.commit()

        val_res = await client.post(f"/api/v1/datasets/{ds_id}/validate")
        assert val_res.status_code == 200
        data = val_res.json()
        assert data["is_valid"] is False
        assert data["error_count"] >= 1
        assert any(
            i["field"] == "context[0]" and i["severity"] == "error"
            for i in data["issues"]
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_validate_nonexistent_dataset_404() -> None:
    async with managed_api_client() as client:
        non_existent_id = uuid.uuid4()
        val_res = await client.post(f"/api/v1/datasets/{non_existent_id}/validate")
        assert val_res.status_code == 404
        assert val_res.json() == {"detail": "Dataset not found"}


@pytest.mark.asyncio
async def test_health_endpoints_remain_operational() -> None:
    async with managed_api_client() as client:
        r1 = await client.get("/api/v1/health")
        assert r1.status_code == 200
        assert r1.json() == {"status": "ok"}

        r2 = await client.get("/api/v1/health/live")
        assert r2.status_code == 200
        assert r2.json() == {"status": "ok"}

        r3 = await client.get("/api/v1/health/ready")
        assert r3.status_code == 200
        assert r3.json() == {"status": "ok"}
