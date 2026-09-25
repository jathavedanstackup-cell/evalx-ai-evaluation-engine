FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install system utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root user
RUN groupadd -g 10001 evalx && \
    useradd -u 10001 -g evalx -s /bin/bash -m evalx && \
    chown -R evalx:evalx /app

# Install uv for fast, deterministic package installation
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /bin/uv

# Copy dependencies specifications
COPY pyproject.toml uv.lock ./

# Install dependencies using frozen lockfile
RUN uv sync --frozen --no-install-project

# Copy application source code
COPY . .

# Install the project itself and ensure non-root ownership
RUN uv sync --frozen && chown -R evalx:evalx /app

USER evalx

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health/live || exit 1

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
