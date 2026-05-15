"""Smoke tests for the Step 7.1 placeholder FastAPI app."""

from __future__ import annotations

from etlx_server.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_health_returns_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_version_contains_server_and_core() -> None:
    resp = client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert "server" in body
    assert "core" in body
    assert body["core"]  # not empty
