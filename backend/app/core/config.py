from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "EVALX"
    environment: str = "development"
    database_url: str | None = None
    test_database_url: str | None = None

    redis_url: str = "redis://localhost:6379/0"
    test_redis_url: str = "redis://localhost:6379/1"
    worker_concurrency: int = 10
    worker_max_retries: int = 3
    worker_retry_delay_seconds: int = 5
    worker_heartbeat_ttl_seconds: int = 30
    worker_heartbeat_interval_seconds: int = 10

    clerk_secret_key: str | None = None
    clerk_jwt_key: str | None = None
    clerk_authorized_parties: str = ""
    cors_allowed_origins: str = "http://localhost:3000,http://localhost:5173"

    # Step 08: API Hardening & Abuse Protection
    rate_limit_enabled: bool = True
    rate_limit_standard_per_minute: int = 120
    rate_limit_runs_per_minute: int = 10
    rate_limit_bulk_per_minute: int = 20
    rate_limit_unauthenticated_per_minute: int = 60
    rate_limit_fail_open_standard: bool = True
    rate_limit_fail_open_public: bool = True
    rate_limit_fail_open_runs: bool = False
    rate_limit_fail_open_bulk: bool = False
    trusted_proxies: str = ""
    max_request_body_bytes: int = 5_242_880  # 5 MB
    security_headers_enabled: bool = True
    hsts_enabled: bool = False

    # Evaluation Scheduling & Automation
    max_active_schedules_per_tenant: int = 20
    min_schedule_interval_minutes: int = 15
    scheduler_poll_interval_seconds: int = 15
    scheduler_max_catchup_hours: int = 24

    # Database Connection Pool Tuning (Step 16)
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_seconds: float = 30.0
    db_pool_recycle_seconds: int = 1800

    # Redis & Timeout Reliability (Step 16)
    redis_socket_timeout_seconds: float = 5.0
    redis_socket_connect_timeout_seconds: float = 5.0
    health_check_timeout_seconds: float = 3.0
    stale_run_timeout_hours: int = 2
    enable_scheduler_in_worker: bool = True

    @property
    def trusted_proxies_list(self) -> list[str]:
        if not self.trusted_proxies.strip():
            return []
        return [p.strip() for p in self.trusted_proxies.split(",") if p.strip()]

    @property
    def authorized_parties_list(self) -> list[str]:
        if not self.clerk_authorized_parties.strip():
            return []
        return [
            p.strip() for p in self.clerk_authorized_parties.split(",") if p.strip()
        ]

    @property
    def cors_origins_list(self) -> list[str]:
        if not self.cors_allowed_origins.strip():
            return ["http://localhost:3000", "http://localhost:5173"]
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()


def check_production_settings(configuration: Settings = settings) -> list[str]:
    """Inspects configuration for production-readiness and returns error messages."""
    errors: list[str] = []
    if configuration.environment.lower() != "production":
        return errors

    # 1. Database validation
    db_url = configuration.database_url or ""
    if not db_url.strip():
        errors.append("DATABASE_URL is required in production environment.")
    elif "evalx_dev_password" in db_url:
        errors.append(
            "Insecure default password 'evalx_dev_password' is forbidden in production."
        )
    elif "@localhost" in db_url or "@127.0.0.1" in db_url:
        errors.append("Localhost DATABASE_URL host is not permitted in production.")

    # 2. Redis validation
    redis_url = configuration.redis_url or ""
    if not redis_url.strip():
        errors.append("REDIS_URL is required in production environment.")
    elif "localhost" in redis_url or "127.0.0.1" in redis_url:
        errors.append("Localhost REDIS_URL host is not permitted in production.")

    # 3. Authentication credentials
    has_clerk_secret = bool(
        configuration.clerk_secret_key
        and "placeholder" not in configuration.clerk_secret_key.lower()
    )
    has_clerk_jwt = bool(
        configuration.clerk_jwt_key
        and "placeholder" not in configuration.clerk_jwt_key.lower()
    )
    if not (has_clerk_secret or has_clerk_jwt):
        errors.append(
            "Production authentication requires valid CLERK_SECRET_KEY or "
            "CLERK_JWT_KEY."
        )

    # 4. CORS validation
    origins = configuration.cors_origins_list
    if "*" in origins:
        errors.append("Wildcard CORS origin '*' is strictly forbidden in production.")
    for origin in origins:
        if "localhost" in origin.lower() or "127.0.0.1" in origin:
            errors.append(
                f"Localhost CORS origin '{origin}' is forbidden in production."
            )

    # 5. Security headers
    if not configuration.security_headers_enabled:
        errors.append("Security headers must be enabled in production.")

    return errors


def validate_production_settings(configuration: Settings = settings) -> None:
    """Enforces production configuration requirements, raising ValueError on failure."""
    errors = check_production_settings(configuration)
    if errors:
        bullet_list = "\n - ".join(errors)
        header = (
            f"Production configuration validation failed with {len(errors)} error(s):"
        )
        raise ValueError(f"{header}\n - {bullet_list}")


def get_sanitized_settings(configuration: Settings = settings) -> dict[str, Any]:
    """Returns application configuration with sensitive secrets and passwords masked."""
    import re

    raw = configuration.model_dump()
    sanitized: dict[str, Any] = {}
    url_pw_pattern = re.compile(r":([^/@]+)@")

    for k, v in raw.items():
        if v is None:
            sanitized[k] = None
        elif any(
            secret_term in k for secret_term in ["secret", "jwt", "key", "password"]
        ):
            sanitized[k] = "***"
        elif "url" in k and isinstance(v, str):
            sanitized[k] = url_pw_pattern.sub(r":***@", v)
        else:
            sanitized[k] = v

    return sanitized


def require_test_database_url(configuration: Settings = settings) -> str:
    test_database_url = configuration.test_database_url
    if test_database_url is None:
        raise RuntimeError("TEST_DATABASE_URL must be configured for database tests")
    if test_database_url == configuration.database_url:
        raise RuntimeError("TEST_DATABASE_URL must differ from DATABASE_URL")
    return test_database_url
