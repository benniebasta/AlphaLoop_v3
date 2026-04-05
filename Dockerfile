# AlphaLoop v3 — Multi-stage Production Dockerfile
# NOTE: MT5 integration requires Windows; this image is for the WebUI + backtesting

# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy only dependency files first (better layer caching)
COPY pyproject.toml ./
RUN pip install --no-cache-dir --prefix=/install .

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY src/ ./src/
COPY alembic.ini ./

# Create non-root user
RUN useradd -m -r alphaloop && \
    mkdir -p /app/data /app/logs && \
    chown -R alphaloop:alphaloop /app

USER alphaloop

# Environment defaults
ENV ENVIRONMENT=production \
    DATABASE_URL=sqlite+aiosqlite:///data/alphaloop.db \
    LOG_LEVEL=INFO \
    DRY_RUN=true \
    PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8090/health || exit 1

EXPOSE 8090

# Default: run WebUI only
CMD ["python", "-m", "alphaloop.main", "--web-only", "--port", "8090"]
