# etlx-server production image. Multi-stage build:
#
#   builder : python:3.11-slim + uv → resolves the workspace (etl-plugins core
#             via path dep + etlx-server itself) into /repo/.venv from uv.lock.
#   runtime : python:3.11-slim with the venv + source tree, runs as a non-root
#             user. Image is ~200 MB; no build tools, no caches.
#
# Build context = repo root (the uv workspace needs both `etl_plugins/` and
# `services/etlx-server/` visible). docker-compose.prod.yml sets that for us;
# for manual `docker build`:
#
#     docker build \
#       -f services/etlx-server/Dockerfile \
#       -t etlx-server:dev \
#       .
#
# Default CMD launches uvicorn. Override for workers:
#
#     docker run … etlx-server:dev etlx-server worker run
#     docker run … etlx-server:dev etlx-server scheduler run
#     docker run … etlx-server:dev etlx-server db upgrade head
#
# The image does NOT auto-migrate on start (matches the k8s init-container
# convention). docker-compose.prod.yml ships a dedicated `etlx-migrate`
# one-shot service that other services `depends_on:`.

# ---------- builder ---------------------------------------------------------
FROM python:3.11-slim AS builder

# Copy uv from its official image — pinned in Dockerfile rather than via
# `pip install uv` so the builder doesn't drag in a pip cache layer.
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build under /app so the venv path matches the runtime stage (uv writes
# absolute shebangs into console scripts; relocating the venv between
# different paths breaks them at runtime — see uv docs §relocatable).
WORKDIR /app

# Copy the bare minimum needed for `uv sync` to resolve. Layer split so
# pure-dependency churn (no source edits) hits the BuildKit cache.
# pyproject.toml + uv.lock drive resolution; READMEs are required because
# hatchling reads them while building the source distributions.
COPY pyproject.toml uv.lock README.md /app/
COPY etl_plugins/ /app/etl_plugins/
COPY services/etlx-server/pyproject.toml services/etlx-server/README.md /app/services/etlx-server/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package etlx-server --no-install-project

# Now copy the rest of the server source and finalize the install.
COPY services/etlx-server /app/services/etlx-server/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package etlx-server

# ---------- runtime ---------------------------------------------------------
FROM python:3.11-slim AS runtime

# `tini` reaps zombie processes that uvicorn workers can leave behind on
# graceful shutdown. `curl` is for the HEALTHCHECK probe — small enough that
# embedding a Python urllib one-liner isn't worth the readability tradeoff.
RUN apt-get update \
    && apt-get install --no-install-recommends -y tini curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash --uid 10001 etlx

WORKDIR /app
COPY --from=builder --chown=etlx:etlx /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SERVICE_NAME=etlx-server

USER etlx
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=20s \
    CMD curl -fsS http://127.0.0.1:8000/ready || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "etlx_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
