# EVALX — AI Evaluation & Reliability Engine

EVALX is an enterprise-grade AI Evaluation and Reliability Engine designed to assess, score, and benchmark LLM applications, RAG pipelines, and agentic workflows.

Step 03 establishes the **Dataset Management & Ingestion Layer**: full CRUD operations, pagination, substring search, bulk ingestion (1–1000 items), dataset validation, and dataset versioning with atomic rollback.

---

## Current Architecture

- **Backend Runtime**: Python 3.14 + FastAPI + async SQLAlchemy 2.x + `psycopg` (v3).
- **Persistence Layer**: PostgreSQL with `pgvector` enabled (`pgvector/pgvector:pg17` image).
- **Database Migrations**: Alembic is the **only** schema-management path; all schema changes are versioned under `backend/alembic`.
- **Domain Persistence Models** (`backend/app/models/`):
  - `User`: Application users with UUID primary key, timestamps, and indexed unique email (`uq_users_email`).
  - `Dataset`: Evaluation datasets with name, description, integer version, and cascade-managed cases.
  - `DatasetCase`: Evaluation test cases with input, expected output, `JSONB` context (supporting RAG passages `list[str]` or structured documents), and `JSONB` metadata.
  - `Evaluation`: Evaluation definitions capturing target model provider, model name, system prompt, and dataset reference.
  - `EvaluatorConfig`: Evaluator configurations (metrics, thresholds, enabled status, and `JSONB` parameters).
  - `EvaluationRun`: Execution runs tracking lifecycle status, started/completed timestamps, and aggregate score.
  - `EvaluationResult`: Granular test-case scores (factuality, relevance, faithfulness, instruction following, consistency, hallucination) with unique constraint `uq_result_run_case`.
- **Schemas & Data Validation** (`backend/app/schemas/`):
  - Strict Pydantic v2 schemas: `DatasetCreate`, `DatasetUpdate`, `DatasetResponse`.
  - Case schemas: `DatasetCaseCreate`, `DatasetCaseUpdate`, `DatasetCaseResponse`, `DatasetCaseBulkCreateRequest`, `DatasetCaseBulkCreateResponse`.
  - Generic pagination: `PaginatedResponse[T]` with `total`, `page`, `page_size`, `total_pages`.
  - Validation schemas: `ValidationIssue`, `DatasetValidationResult`.
- **Service Layer** (`backend/app/services/dataset_service.py`):
  - Encapsulated business logic for dataset CRUD, case counts (via `outerjoin` to prevent N+1 queries), pagination, bulk ingestion, and validation.
- **Database & Session Infrastructure** (`backend/app/database/`):
  - Typed `DeclarativeBase` (`Base`).
  - Lazily cached async engine (`get_async_engine`) and session factory (`get_sessionmaker`).
  - Async FastAPI dependency (`get_async_session`) providing clean request-scoped sessions.
  - Engine teardown helper (`dispose_async_engine`).
- **Configuration & Isolation** (`backend/app/core/config.py`):
  - `Settings` manages environment variables with Pydantic Settings.
  - Strict database isolation enforced: `require_test_database_url` ensures tests fail loudly rather than falling back to `DATABASE_URL`.

---

## Database Architecture & Distinction

EVALX strictly isolates application/development data from integration test execution:

| Database | Connection Variable | Intended Purpose | Safety Policy |
|---|---|---|---|
| **Development** (`evalx`) | `DATABASE_URL` | Local API runtime, manual testing, development data | Preserved across test runs; never modified by automated test suites. |
| **Test** (`evalx_test`) | `TEST_DATABASE_URL` | Integration test execution, schema validation | Subject to automated table truncation and test-data teardown; tests **never** fall back to `DATABASE_URL`. |

---

## Dataset Management & Ingestion API

All dataset endpoints are mounted under `/api/v1/datasets`:

### Datasets
| Method | Path | Description | Status Code |
|---|---|---|---|
| `POST` | `/api/v1/datasets` | Create a dataset (`name`, `description`) | `201 Created` |
| `GET` | `/api/v1/datasets` | List datasets with pagination (`page`, `page_size`) and search (`search`) | `200 OK` |
| `GET` | `/api/v1/datasets/{dataset_id}` | Retrieve dataset by ID with total `case_count` | `200 OK` / `404` |
| `PATCH` | `/api/v1/datasets/{dataset_id}` | Update dataset name or description | `200 OK` / `404` |
| `DELETE` | `/api/v1/datasets/{dataset_id}` | Delete dataset and cascade delete all associated cases | `204 No Content` / `404` |

### Test Cases
| Method | Path | Description | Status Code |
|---|---|---|---|
| `POST` | `/api/v1/datasets/{dataset_id}/cases` | Create a single test case (`input`, `expected_output`, `context`, `metadata`) | `201 Created` / `404` |
| `GET` | `/api/v1/datasets/{dataset_id}/cases` | List test cases with pagination (`page`, `page_size`) | `200 OK` / `404` |
| `GET` | `/api/v1/datasets/{dataset_id}/cases/{case_id}` | Retrieve a test case by ID | `200 OK` / `404` |
| `PATCH` | `/api/v1/datasets/{dataset_id}/cases/{case_id}` | Update test case fields | `200 OK` / `404` |
| `DELETE` | `/api/v1/datasets/{dataset_id}/cases/{case_id}` | Delete a test case | `204 No Content` / `404` |

### Bulk Ingestion & Validation
| Method | Path | Description | Status Code |
|---|---|---|---|
| `POST` | `/api/v1/datasets/{dataset_id}/cases/bulk` | Bulk ingest cases (1–1000 items) in an atomic transaction | `201 Created` / `422` / `404` |
| `POST` | `/api/v1/datasets/{dataset_id}/validate` | Run validation suite: completeness, duplicate detection, schema compliance | `200 OK` / `404` |

---

## Health & Probe Endpoints

EVALX exposes layered health probes:

- `GET /`: Application metadata (`name`, `status`, `version`).
- `GET /api/v1/health`: Basic API health check (always returns HTTP 200 `{"status": "ok"}`).
- `GET /api/v1/health/live`: Liveness probe for orchestrators (Kubernetes/Docker). Verifies that the FastAPI process is responsive without querying the database.
- `GET /api/v1/health/ready`: Readiness probe for traffic routing. Performs an async `SELECT 1` query via the database session dependency:
  - Returns **HTTP 200** `{"status": "ok"}` when the database is reachable.
  - Returns **HTTP 503** `{"detail": "Database unavailable"}` when PostgreSQL is unreachable or failing. Internal connection details and credentials are never exposed in error responses.

---

## Local Development Setup

### Prerequisites
- [Docker](https://www.docker.com/) and Docker Compose
- [Python 3.14+](https://www.python.org/)
- [uv](https://github.com/astral-sh/uv) package manager

### 1. Start the PostgreSQL Service

```powershell
docker compose up -d
```

The container automatically creates:
- The development database `evalx` (owned by user `evalx`).
- The test database `evalx_test` (owned by user `evalx_test` using least privilege; `pgvector` extension is pre-installed via container admin context without elevating `evalx_test` to superuser).

### 2. Configure Environment Variables

Create `backend/.env` from `backend/.env.example`:

```dotenv
APP_NAME=EVALX
ENVIRONMENT=development
DATABASE_URL=postgresql+psycopg://evalx:evalx_dev_password@localhost:5432/evalx
TEST_DATABASE_URL=postgresql+psycopg://evalx_test:evalx_test_password@localhost:5432/evalx_test
```

### 3. Install Dependencies & Run Database Migrations

From the `backend/` directory:

```powershell
cd backend
uv sync
uv run alembic upgrade head
```

This applies:
- `0001_initial_schema`: `vector` extension and initial 7 domain tables.
- `0002_add_dataset_version`: `version` column on `datasets` table (default 1).

### 4. Run the Quality Gates & Test Suite

Run unit tests and database integration tests:

```powershell
uv run pytest
```

Run code formatting and type checking:

```powershell
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run mypy tests
```

Validate Docker Compose configuration from repository root:

```powershell
docker compose config
```

### 5. Run the FastAPI Development Server

```powershell
uv run fastapi dev app/main.py
```

The API will be available at `http://localhost:8000`. Interactive documentation is accessible at `http://localhost:8000/docs`.

---

## Migration Commands Reference

All migration commands must be run from `backend/`:

- **Apply all migrations**:
  ```powershell
  uv run alembic upgrade head
  ```
- **Check current revision**:
  ```powershell
  uv run alembic current
  ```
- **Roll back one revision**:
  ```powershell
  uv run alembic downgrade -1
  ```

---

## Production Deployment & Operations

For enterprise production deployment guidelines, container hardening, topology design, disaster recovery procedures, and security policies, see:
- [Production Deployment & Reliability Guide](docs/deployment.md)

