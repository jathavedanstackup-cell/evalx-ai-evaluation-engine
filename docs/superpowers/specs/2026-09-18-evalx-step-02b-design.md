# EVALX Step 02B Database Foundation Design

## Scope

Step 02B adds PostgreSQL persistence infrastructure only. It preserves the existing root and health API contract and excludes evaluation execution, LLM access, Redis, authentication, business APIs, and frontend work.

## Database and configuration

The application uses PostgreSQL through SQLAlchemy 2.x's async `psycopg` dialect. `Settings` supplies `DATABASE_URL` for application runtime and a separate `TEST_DATABASE_URL` for integration tests. Test fixtures require the test URL explicitly, reject a URL equal to the development URL, and reset schema only on that dedicated target.

## Persistence model

A single typed declarative base owns metadata. Seven models use UUID primary keys, UTC timezone-aware timestamps, explicit foreign keys, bidirectional relationships, JSONB only for flexible PostgreSQL document fields, and indexes on joins and primary query paths. Alembic is the sole schema-management mechanism and its async environment imports all model modules before exposing the base metadata.

## Operations

Docker Compose runs a pgvector PostgreSQL image with configurable local credentials, a named volume, and a database healthcheck. The initial migration installs `vector` and creates the seven tables, constraints, and indexes. The readiness endpoint performs `SELECT 1` through the async session dependency and reports 503 without exposing internals on failure.

## Tests

Unit/API tests run without a database except readiness-success integration coverage. Database integration tests use `TEST_DATABASE_URL` only, create schema by invoking the Alembic migration, and reset that schema between test runs. A deliberately unavailable dependency override validates readiness failure without contacting any configured database.
