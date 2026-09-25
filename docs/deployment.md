# EVALX Production Deployment & Reliability Guide

## 1. Production Architecture Overview

EVALX is designed as a modular, stateless, horizontally scalable evaluation engine backed by persistent PostgreSQL (with `pgvector`) and an in-memory Redis cluster for asynchronous job coordination and distributed heartbeats.

```mermaid
flowchart TD
    Internet["Public Traffic / Clients"] --> Ingress["Reverse Proxy / Ingress (TLS Termination)"]
    
    subgraph AppNetwork ["Internal Isolated Network"]
        Ingress -->|Port 8000| API["API Service (FastAPI / Non-Root)"]
        
        API -->|Database Pool (Port 5432)| Postgres[("PostgreSQL 17 + pgvector<br/>Persistent Volume")]
        API -->|Queue Submissions (Port 6379)| Redis[("Redis 7 (ARQ Queue & Heartbeat)<br/>Persistent Volume")]
        
        Worker["Worker Service (ARQ)<br/>Scalable Workers"] -->|Poll Jobs| Redis
        Worker -->|Fetch/Persist Runs| Postgres
        
        Scheduler["Scheduler Service (Dedicated)<br/>Recurrence Poller"] -->|Claim FOR UPDATE SKIP LOCKED| Postgres
        Scheduler -->|Enqueue Scheduled Runs| Redis
    end
```

### Core Services Topology
- **API Service**: Handles REST endpoints, authentication (Clerk JWT/Secret), request size bounding, rate limiting, and database interactions.
- **Worker Service**: Executes asynchronous evaluations (LLM judges, DeepEval metrics, consistency checks) via ARQ. Scalable horizontally.
- **Scheduler Service**: Dedicated container running `app.worker.scheduler_runner`. Polls due schedules concurrency-safely (`FOR UPDATE SKIP LOCKED`) and enqueues runs.
- **PostgreSQL Database**: Authoritative persistence for datasets, test cases, configuration presets, runs, results, schedules, and audit events.
- **Redis**: Ephemeral queue coordination, job retries, and worker heartbeats.

---

## 2. Production Configuration & Environment Variables

### Mandatory Production Settings
When `ENVIRONMENT=production`, startup validation fails fast and halts container launch if any of the following constraints are violated:

| Variable | Description | Production Requirement |
| :--- | :--- | :--- |
| `ENVIRONMENT` | Application operational mode | Must be set to `production` |
| `DATABASE_URL` | PostgreSQL async connection string | Must not point to `localhost` and must not contain dev default passwords (`evalx_dev_password`) |
| `REDIS_URL` | Redis connection string | Must not point to `localhost` |
| `CLERK_SECRET_KEY` / `CLERK_JWT_KEY` | Authentication keys | Must be provided with genuine credentials (placeholders rejected) |
| `CORS_ALLOWED_ORIGINS` | Comma-separated allowed web origins | Must be explicit origins; wildcard `*` and `localhost` are strictly forbidden |
| `SECURITY_HEADERS_ENABLED` | Browser security headers | Must be `true` |

### Database & Queue Tuning Parameters
| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `DB_POOL_SIZE` | `10` | Base connection pool capacity per container |
| `DB_MAX_OVERFLOW` | `20` | Max temporary burst connections beyond pool size |
| `DB_POOL_TIMEOUT_SECONDS` | `30.0` | Max seconds to wait for an available pool connection |
| `DB_POOL_RECYCLE_SECONDS` | `1800` | Recycles idle connections after 30m to avoid firewall drops |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | `5.0` | Socket read/write timeout for Redis operations |
| `REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS` | `5.0` | Socket connection establishment timeout |
| `HEALTH_CHECK_TIMEOUT_SECONDS` | `3.0` | Strict timeout ceiling for database/redis readiness probes |
| `STALE_RUN_TIMEOUT_HOURS` | `2` | Ceiling before an uncompleted active run is marked failed |
| `ENABLE_SCHEDULER_IN_WORKER` | `false` | Disabled when running dedicated `scheduler` service |

---

## 3. Container & Host Hardening

1. **Non-Root Execution**:
   - The production container creates and switches to a dedicated unprivileged user `evalx` (UID 10001, GID 10001).
   - No container processes run as `root`.
2. **Network Isolation**:
   - Internal infrastructure services (PostgreSQL, Redis) bind exclusively to `127.0.0.1` on the host, preventing accidental exposure to public interfaces.
   - Container-to-container communication occurs over the Docker internal bridge network.
3. **Build Hygiene & `.dockerignore`**:
   - Secrets (`.env`, `.env.*`), git histories, test caches, virtualenvs, and log files are excluded from image builds.
4. **Health Checks**:
   - `API`: Native HTTP GET `/api/v1/health/live` check.
   - `PostgreSQL`: `pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB`.
   - `Redis`: `redis-cli ping`.

---

## 4. Health Probes & Operational Semantics

| Endpoint | Probe Type | Behavior & Failure Semantics |
| :--- | :--- | :--- |
| `GET /api/v1/health/live` | **Liveness** | Verifies process responsiveness. **Never** touches DB or Redis. Returns `200 OK` instantly. |
| `GET /api/v1/health/ready` | **Readiness** | Probes DB (`SELECT 1`) and Redis (`PING`) with strict 3-second timeouts. Returns `503 Service Unavailable` if either dependency fails or times out. |
| `GET /api/v1/health/worker` | **Worker Health** | Inspects Redis for fresh worker heartbeats. Returns `healthy`, `degraded`, or `unhealthy`. |

---

## 5. Database Migration Runbook

Alembic is the **sole** schema management authority. Schema changes must **never** be executed automatically on application startup.

### Pre-Deployment Migration Procedure
1. Create a point-in-time PostgreSQL backup:
   ```bash
   pg_dump -Fc -h postgres -U evalx evalx > pre_migration_backup.dump
   ```
2. Inspect target migration state:
   ```bash
   uv run alembic current
   uv run alembic heads
   ```
3. Apply migrations deterministically:
   ```bash
   uv run alembic upgrade head
   ```
4. Verify migration state matches domain models:
   ```bash
   uv run alembic check
   ```

### Rollback Strategy
If a migration fails or must be rolled back:
```bash
uv run alembic downgrade -1
```
*Note: Ensure schema changes maintain backward compatibility during zero-downtime rolling deployments.*

---

## 6. Backup & Disaster Recovery

### PostgreSQL Durable State
- **Critical Data**: Datasets, test cases, configuration presets, evaluation runs, test case results, schedules, schedule executions, and append-only audit events.
- **Backup Command**:
  ```bash
  pg_dump -Fc -h <HOST> -U <USER> -d evalx -f "evalx_backup_$(date +%Y%m%d_%H%M%S).dump"
  ```
- **Restore Command**:
  ```bash
  pg_restore -h <HOST> -U <USER> -d evalx --clean --if-exists "evalx_backup_<TIMESTAMP>.dump"
  ```

### Redis Ephemeral State
- **State Classification**: Redis holds ephemeral ARQ job queues and short-lived worker heartbeats.
- **Loss Recovery**: If Redis experiences total data loss, active runs that were in-flight will be automatically reclaimed on worker restart by the `reconcile_stale_runs()` startup process. Durable run history and results in PostgreSQL remain intact.

### Worker & Scheduler Recovery
- **Stalled Worker Recovery**: If a worker node crashes or is OOM-killed, runs left in `RUNNING` or `IN_PROGRESS` beyond `STALE_RUN_TIMEOUT_HOURS` are automatically marked `FAILED` on worker startup.
- **Scheduler Recovery**: If the scheduler is down, `FOR UPDATE SKIP LOCKED` guarantees no race conditions upon recovery. Any schedules overdue beyond 24 hours are marked `MISSED` with `SCHEDULE_WINDOW_EXPIRED` to prevent cascade queue overload.

---

## 7. Verified Guarantees vs Documented Operational Assumptions

### Verified Guarantees (Tested in Quality Gates)
1. **Zero Secret Leakage**: Database passwords, Redis URLs, and Clerk secrets are never stringified or emitted in application logs, error payloads, or configuration inspection APIs.
2. **Tenant Isolation**: Foreign tenants attempting to query foreign datasets, configs, runs, schedules, or audit logs strictly receive `404 Not Found`.
3. **Safe Liveness**: Liveness probes never fail due to database or Redis outages.
4. **Concurrency Safety**: Recurring schedules are claimed safely with `FOR UPDATE SKIP LOCKED` without double execution across parallel instances.
5. **Bounded Overhead**: Request bodies are capped at 5MB; audit event queries and schedule listings are paginated with bounded maximum limits.

### Documented Operational Assumptions
1. **TLS / SSL Termination**: It is assumed that external HTTPS traffic terminates at an upstream reverse proxy (e.g. Nginx, Traefik, AWS ALB, Cloudflare) before reaching the API service on port 8000.
2. **PostgreSQL HA**: For enterprise mission-critical availability, PostgreSQL should be deployed as a managed cluster with streaming replication and automated failover.

