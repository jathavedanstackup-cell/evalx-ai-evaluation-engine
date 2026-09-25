import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import require_test_database_url, settings
from app.database.session import (
    dispose_async_engine,
    get_async_engine,
    get_async_session,
    get_sessionmaker,
)
from app.main import app
from app.models import Dataset, DatasetCase


@asynccontextmanager
async def managed_test_session() -> AsyncIterator[AsyncSession]:
    # Enforces TEST_DATABASE_URL and prevents fallback to DATABASE_URL
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        # Deterministic cleanup between tests
        await session.rollback()
        await session.execute(
            text(
                "TRUNCATE TABLE users, datasets, dataset_cases, "
                "evaluations, evaluator_configs, evaluation_runs, "
                "evaluation_results CASCADE"
            )
        )
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_engine_raises_when_database_url_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await dispose_async_engine()
    monkeypatch.setattr(settings, "database_url", None)

    with pytest.raises(RuntimeError, match="DATABASE_URL must be configured"):
        get_async_engine()

    with pytest.raises(RuntimeError, match="DATABASE_URL must be configured"):
        get_sessionmaker()


@pytest.mark.asyncio
async def test_get_engine_returns_engine_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await dispose_async_engine()
    monkeypatch.setattr(
        settings, "database_url", "postgresql+psycopg://user:pass@localhost:5432/evalx"
    )

    engine = get_async_engine()
    assert isinstance(engine, AsyncEngine)
    assert get_async_engine() is engine

    sessionmaker = get_sessionmaker()
    assert get_sessionmaker() is sessionmaker

    await dispose_async_engine()


@pytest.mark.asyncio
async def test_test_database_connectivity() -> None:
    async with managed_test_session() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1


@pytest.mark.asyncio
async def test_test_database_schema_and_extensions() -> None:
    async with managed_test_session() as session:
        # Verify pgvector extension is present in the test database
        ext_result = await session.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        )
        assert ext_result.scalar() == "vector"

        # Verify all seven domain tables exist in the public schema
        tables_result = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        existing_tables = {row[0] for row in tables_result.fetchall()}
        expected_tables = {
            "users",
            "datasets",
            "dataset_cases",
            "evaluations",
            "evaluator_configs",
            "evaluation_runs",
            "evaluation_results",
        }
        assert expected_tables.issubset(existing_tables)


@pytest.mark.asyncio
async def test_dataset_crud() -> None:
    async with managed_test_session() as session:
        # Create
        dataset = Dataset(
            name="Customer Support Q&A",
            description="Dataset containing real customer queries",
        )
        session.add(dataset)
        await session.commit()
        dataset_id = dataset.id
        assert isinstance(dataset_id, uuid.UUID)

        # Read
        query = select(Dataset).where(Dataset.id == dataset_id)
        result = await session.execute(query)
        fetched = result.scalar_one_or_none()
        assert fetched is not None
        assert fetched.name == "Customer Support Q&A"
        assert fetched.description == "Dataset containing real customer queries"
        assert fetched.created_at is not None
        assert fetched.updated_at is not None

        # Update
        fetched.description = "Updated description for testing"
        await session.commit()

        updated_result = await session.execute(query)
        updated = updated_result.scalar_one_or_none()
        assert updated is not None
        assert updated.description == "Updated description for testing"

        # Delete
        await session.delete(updated)
        await session.commit()

        deleted_result = await session.execute(query)
        assert deleted_result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_dataset_case_crud() -> None:
    async with managed_test_session() as session:
        # Setup parent dataset
        dataset = Dataset(name="RAG Benchmark")
        session.add(dataset)
        await session.commit()

        # Create case with list of strings context and JSON metadata
        case = DatasetCase(
            dataset_id=dataset.id,
            input="Where is the Eiffel Tower?",
            expected_output="Paris, France",
            context=[
                "The Eiffel Tower is a wrought-iron lattice tower in Paris, France."
            ],
            metadata_={"difficulty": "easy", "language": "en"},
        )
        session.add(case)
        await session.commit()
        case_id = case.id
        assert isinstance(case_id, uuid.UUID)

        # Read
        query = select(DatasetCase).where(DatasetCase.id == case_id)
        result = await session.execute(query)
        fetched = result.scalar_one_or_none()
        assert fetched is not None
        assert fetched.input == "Where is the Eiffel Tower?"
        assert fetched.expected_output == "Paris, France"
        assert fetched.context == [
            "The Eiffel Tower is a wrought-iron lattice tower in Paris, France."
        ]
        assert fetched.metadata_ == {"difficulty": "easy", "language": "en"}

        # Update
        fetched.expected_output = "Paris"
        await session.commit()

        updated_result = await session.execute(query)
        updated = updated_result.scalar_one_or_none()
        assert updated is not None
        assert updated.expected_output == "Paris"

        # Delete
        await session.delete(updated)
        await session.commit()

        deleted_result = await session.execute(query)
        assert deleted_result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_dataset_cascade_deletes_cases() -> None:
    async with managed_test_session() as session:
        dataset = Dataset(name="Cascade Parent")
        case1 = DatasetCase(
            input="Query 1",
            expected_output="Answer 1",
        )
        case2 = DatasetCase(
            input="Query 2",
            expected_output="Answer 2",
        )
        dataset.cases.extend([case1, case2])
        session.add(dataset)
        await session.commit()

        case1_id = case1.id
        case2_id = case2.id

        # Verify cases exist
        query = select(DatasetCase).where(DatasetCase.id.in_([case1_id, case2_id]))
        result = await session.execute(query)
        assert len(result.scalars().all()) == 2

        # Delete parent dataset
        await session.delete(dataset)
        await session.commit()

        # Verify child cases were cascade deleted
        result_after = await session.execute(query)
        assert len(result_after.scalars().all()) == 0


@pytest.mark.asyncio
async def test_dataset_case_foreign_key_constraint() -> None:
    async with managed_test_session() as session:
        # Attempting to insert a case with a non-existent dataset_id must fail FK check
        orphan_case = DatasetCase(
            dataset_id=uuid.uuid4(),
            input="Orphan input",
        )
        session.add(orphan_case)
        with pytest.raises((IntegrityError, DBAPIError)):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_health_live_endpoint() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_ready_endpoint_success_with_test_db() -> None:
    test_url = require_test_database_url(settings)
    engine = create_async_engine(test_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def get_test_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = get_test_db_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_ready_endpoint_failure_when_unreachable() -> None:
    class BrokenSession:
        async def execute(self, _statement: object) -> None:
            raise SQLAlchemyError("Connection refused")

    async def get_broken_session() -> BrokenSession:
        return BrokenSession()

    app.dependency_overrides[get_async_session] = get_broken_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    # Verify no credentials or internal details are leaked
    assert response.json() == {"detail": "Database unavailable"}
