"""Readiness probe — real metadata DB connection via testcontainers (Step 8.1).

The /ready endpoint pings the DB with ``SELECT 1``. We use an
``httpx.AsyncClient`` over ``ASGITransport`` so the FastAPI lifespan fires.
"""

from __future__ import annotations

import httpx
import pytest
from anyduct_server.app_factory import create_app
from anyduct_server.settings import Settings
from httpx import ASGITransport

pytestmark = pytest.mark.asyncio


async def _send(app: object, path: str) -> httpx.Response:
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with (
        app.router.lifespan_context(app),  # type: ignore[attr-defined]
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        return await client.get(path)


async def test_ready_reports_ok_when_db_is_reachable(metadata_db_url: str) -> None:
    app = create_app(settings=Settings(database_url=metadata_db_url))
    resp = await _send(app, "/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"


async def test_ready_reports_degraded_when_db_is_unreachable() -> None:
    # Pointing at a closed local port — connect fails fast.
    bad_url = "postgresql+asyncpg://nope:nope@127.0.0.1:1/none"
    app = create_app(settings=Settings(database_url=bad_url))
    resp = await _send(app, "/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["database"] == "error"
    assert body["detail"]
