FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir hatchling

FROM base AS production

# AUDIT-A5: deterministic install from the same universal lockfile CI uses
# (DS-20260527-V5). `pip install .` resolved pyproject ranges live at build
# time → non-deterministic image, MAL/CVE-hardening bypassed. Install pinned
# deps from the lock first (cached layer), then the project itself --no-deps.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml .
COPY README.md .
COPY app/ ./app/
COPY monitor/ ./monitor/
COPY alembic.ini .

RUN pip install --no-cache-dir . --no-deps

RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
