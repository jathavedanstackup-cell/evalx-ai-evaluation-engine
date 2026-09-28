import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.audit import router as audit_router
from app.api.v1.datasets import router as datasets_router
from app.api.v1.evaluation_configs import router as evaluation_configs_router
from app.api.v1.evaluation_runs import router as evaluation_runs_router
from app.api.v1.evaluation_schedules import router as evaluation_schedules_router
from app.api.v1.experiments import router as experiments_router
from app.api.v1.health import router as health_router
from app.api.v1.regression_gates import router as regression_gates_router
from app.core.config import settings, validate_production_settings
from app.core.errors import register_error_handlers
from app.database.session import dispose_async_engine
from app.security.middleware import (
    CorrelationIdMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage app lifecycle: validate production config, seed initial data, and dispose DB engine."""
    logger.info("Starting EVALX application (environment=%s)", settings.environment)
    if settings.environment.lower() == "production":
        validate_production_settings(settings)
    try:
        from app.database.seeder import seed_production_benchmarks
        await seed_production_benchmarks()
    except Exception as seed_err:
        logger.warning("Database seeding during startup skipped or encountered non-fatal error: %s", seed_err)
    yield
    logger.info("Shutting down EVALX application...")
    await dispose_async_engine()
    logger.info("EVALX application shutdown complete.")


app = FastAPI(
    title="EVALX",
    description="AI Evaluation & Reliability Engine",
    version="0.1.0",
    lifespan=lifespan,
)

cors_origins = settings.cors_origins_list
# Browser standards forbid allow_credentials=True when wildcard '*' is allowed
allow_creds = "*" not in cors_origins

# Register middlewares (CorrelationId -> SecurityHeaders -> RequestSizeLimit -> CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=allow_creds,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CorrelationIdMiddleware)

# Register hardened global exception handlers
register_error_handlers(app)

app.include_router(health_router, prefix="/api/v1")
app.include_router(audit_router, prefix="/api/v1")
app.include_router(datasets_router, prefix="/api/v1")
app.include_router(evaluation_runs_router, prefix="/api/v1")
app.include_router(evaluation_configs_router, prefix="/api/v1")
app.include_router(regression_gates_router, prefix="/api/v1")
app.include_router(experiments_router, prefix="/api/v1")
app.include_router(evaluation_schedules_router, prefix="/api/v1")


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "EVALX",
        "status": "running",
        "version": "0.1.0",
    }
