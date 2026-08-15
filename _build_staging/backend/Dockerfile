# syntax=docker/dockerfile:1
#
# Build context is expected to be the repository root, e.g.:
#   docker build -f backend/Dockerfile -t genie-backend .
#
# Phase 1 scope: this image runs the FastAPI application bootstrap and
# fail-closed startup validation only. It intentionally contains no
# hardcoded configuration, secrets, or endpoints; all runtime configuration
# is supplied via environment variables (see backend/.env.example).

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

FROM base AS builder

COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app

RUN pip install --no-cache-dir .

FROM base AS runtime

RUN groupadd --system genie && useradd --system --gid genie --no-create-home genie

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY backend/app ./app
COPY config ./config

# Deploy & Launch's DeploymentPipelineService materializes each mission's
# generated build under a relative workspace root (Settings.
# deployment_build_workspace_root, default "var/deploy-launch-builds",
# resolved against the process cwd = WORKDIR /app) before it can be
# packaged/deployed - that directory must exist and be writable by the
# non-root "genie" user this image runs as (/app itself is root-owned from
# the COPY steps above), or every Deploy & Launch run fails closed with
# "[Errno 13] Permission denied: 'var'" on its very first write.
RUN mkdir -p /app/var/deploy-launch-builds && chown -R genie:genie /app/var

USER genie

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
