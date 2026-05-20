.DEFAULT_GOAL := help
.PHONY: help setup sync test test-it lint fmt typecheck up down clean pre-commit docs docs-serve etlx seed-schema bundle-db bundle-db-demo

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup:  ## One-shot environment bootstrap (uv + .env + pre-commit + docker)
	./scripts/bootstrap.sh

sync:  ## Sync dependencies from lockfile
	uv sync

test:  ## Run unit tests
	uv run pytest -m "not it"

test-it:  ## Run integration tests (requires docker)
	uv run pytest -m it

test-all:  ## Run all tests with coverage
	uv run pytest --cov=etl_plugins --cov-report=term-missing

lint:  ## Lint (ruff check + mypy)
	uv run ruff check .
	uv run mypy etl_plugins

fmt:  ## Format (ruff format + fix)
	uv run ruff format .
	uv run ruff check . --fix

typecheck:  ## mypy only
	uv run mypy etl_plugins

pre-commit:  ## Run all pre-commit hooks
	uv run pre-commit run --all-files

up:  ## Start local dev infra (postgres / kafka / minio)
	docker compose -f docker/docker-compose.dev.yml up -d

down:  ## Stop local dev infra
	docker compose -f docker/docker-compose.dev.yml down

logs:  ## Tail dev infra logs
	docker compose -f docker/docker-compose.dev.yml logs -f

etlx:  ## Run unified `etlx` CLI (forwards to scripts/etlx); pass args via ARGS=
	@./scripts/etlx $(ARGS)

seed-schema:  ## Regenerate services/etlx-postgres/init/00-schema.sql from current Alembic head
	services/etlx-postgres/regen-schema.sh

bundle-db:  ## Build etlx-postgres:dev (metadata DB image with schema pre-applied)
	docker build -f services/etlx-postgres/Dockerfile \
	    -t etlx-postgres:dev services/etlx-postgres/

bundle-db-demo:  ## Build etlx-postgres:demo (schema + demo workspace + demo@example.com)
	docker build -f services/etlx-postgres/Dockerfile \
	    --build-arg INCLUDE_DEMO_SEED=1 \
	    -t etlx-postgres:demo services/etlx-postgres/

docs:  ## Build static docs site (strict — fails on broken refs)
	uv run mkdocs build --strict

docs-serve:  ## Live-reload docs at http://127.0.0.1:8000
	uv run mkdocs serve

clean:  ## Remove caches and build artifacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	rm -rf dist build *.egg-info
