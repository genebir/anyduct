"""Unit tests for the FastAPI bootstrap (Step 8.1).

Uses an explicit ``create_app(settings=...)`` so tests don't depend on the
real ``DATABASE_URL`` env var. The readiness endpoint is exercised against a
real Postgres container in
``services/etlx-server/tests/db/test_health_ready.py``.
"""

from __future__ import annotations

import pytest
from etlx_server.app_factory import create_app
from etlx_server.settings import Environment, Settings
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    """App built with stub settings — no DB needed for /health and /version."""
    settings = Settings(
        database_url="postgresql+asyncpg://stub:stub@stub:5432/stub",
        environment=Environment.DEVELOPMENT,
    )
    return TestClient(create_app(settings=settings))


def test_health_returns_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_version_contains_server_core_and_environment(client: TestClient) -> None:
    resp = client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert {"server", "core", "service", "environment"} <= set(body)
    assert body["core"]  # not empty
    assert body["environment"] == "development"


def test_docs_enabled_in_development(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200


def test_docs_hidden_in_production() -> None:
    prod_app = create_app(
        settings=Settings(
            database_url="postgresql+asyncpg://stub:stub@stub:5432/stub",
            environment=Environment.PRODUCTION,
        )
    )
    prod_client = TestClient(prod_app)
    assert prod_client.get("/docs").status_code == 404
    assert prod_client.get("/redoc").status_code == 404


def test_cors_middleware_attached_when_origins_set() -> None:
    cors_app = create_app(
        settings=Settings(
            database_url="postgresql+asyncpg://stub:stub@stub:5432/stub",
            cors_origins=["https://example.com"],
        )
    )
    cors_client = TestClient(cors_app)
    # Preflight from an allowed origin returns the access-control header.
    resp = cors_client.options(
        "/health",
        headers={
            "origin": "https://example.com",
            "access-control-request-method": "GET",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == "https://example.com"


def test_openapi_schema_lists_endpoints(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = set(schema["paths"])
    assert {"/health", "/ready", "/version"} <= paths
