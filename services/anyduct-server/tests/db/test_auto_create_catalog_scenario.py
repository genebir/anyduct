"""Cross-DB ``auto_create_table`` → catalog REST visibility (Phase AAJ).

When a pipeline runs with ``auto_create_table=true``, the runtime
creates the destination table on its own. The expectation we want to
pin down is: the auto-created table shows up in the catalog REST
exactly like a hand-rolled sink — it's a first-class asset, not a
runtime-only side-effect.

Without this guarantee, an analyst building a dashboard off the
cache wouldn't see it in the catalog's lineage graph and would assume
the run never happened.

The scenario seeds a sample source, runs the cross-DB pipeline with
``auto_create_table=true``, then hits ``GET /workspaces/{ws}/assets``
with a Viewer's token and asserts the new sink's asset key is present
with the expected ``upstream`` / ``downstream`` links.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from anyduct_server.app_factory import create_app
from anyduct_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from anyduct_server.auth.password_service import PasswordService
from anyduct_server.db.enums import AuthMethod, RunStatus, WorkspaceRole
from anyduct_server.db.models import Membership, Run, User, Workspace
from anyduct_server.dependencies import get_session
from anyduct_server.settings import Settings
from anyduct_server.worker.claim import claim_pending_run
from anyduct_server.worker.executor import RunExecutor
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import StaticSecretBackend
from tests.db.test_worker_lifecycle import (
    _seed_connection,
    _seed_pending_run,
    _seed_pipeline,
    _SessionFactoryAdapter,
)

pytestmark = pytest.mark.asyncio


def _build_app(session: AsyncSession) -> FastAPI:
    private, public = generate_rsa_keypair_pem(bits=2048)
    settings = Settings(
        database_url="postgresql+asyncpg://stub:stub@stub:5432/stub",  # pragma: allowlist secret
        auth_jwt_private_key_pem=private.decode("utf-8"),
        auth_jwt_public_key_pem=public.decode("utf-8"),
        auth_jwt_access_ttl_seconds=60,
        auth_jwt_refresh_ttl_seconds=120,
    )
    app = create_app(settings=settings)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override_session
    app.state.password_service = PasswordService(rounds=4)
    app.state.jwt_service = JwtService(
        private_key_pem=private,
        public_key_pem=public,
        issuer=settings.auth_jwt_issuer,
        audience=settings.auth_jwt_audience,
        access_ttl_seconds=settings.auth_jwt_access_ttl_seconds,
        refresh_ttl_seconds=settings.auth_jwt_refresh_ttl_seconds,
    )
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        email=email.lower(),
        name="U",
        auth_method=AuthMethod.LOCAL,
        password_hash=PasswordService(rounds=4).hash("hunter2"),  # pragma: allowlist secret
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_ws_with_member(
    session: AsyncSession, *, slug: str, user: User, role: WorkspaceRole
) -> Workspace:
    ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
    session.add(ws)
    await session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=user.id, role=role))
    await session.flush()
    return ws


async def _login(client: httpx.AsyncClient, *, email: str) -> str:
    resp = await client.post(
        "/auth/login",
        json={"email": email, "password": "hunter2"},  # pragma: allowlist secret
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


async def _run_pending(session: AsyncSession, worker_id: str) -> Run:
    claimed = await claim_pending_run(session, worker_id=worker_id)
    assert claimed is not None
    await session.commit()
    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    ).execute(claimed.id)
    row = await session.execute(select(Run).where(Run.id == claimed.id))
    return row.scalar_one()


async def test_aaj1_auto_created_sink_lands_in_catalog_via_rest(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Auto-created sink table is visible through ``GET /assets`` with
    the right upstream link, exactly like a hand-rolled sink."""
    src_path = tmp_path / "src.db"
    dst_path = tmp_path / "dst.db"
    raw = sqlite3.connect(str(src_path))
    try:
        raw.execute("CREATE TABLE products (id INTEGER, name TEXT)")
        raw.executemany(
            "INSERT INTO products VALUES (?, ?)",
            [(1, "widget"), (2, "gadget")],
        )
        raw.commit()
    finally:
        raw.close()

    user = await _seed_user(session, email="aaj1@example.com")
    ws = await _seed_ws_with_member(session, slug="aaj1", user=user, role=WorkspaceRole.VIEWER)
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(src_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(dst_path)}
    )
    p, pv = await _seed_pipeline(
        session,
        workspace_id=ws.id,
        name="cache",
        config={
            "name": "products_cache",
            "source": {"connection": "src", "query": "SELECT id, name FROM products"},
            "sink": {
                "connection": "dst",
                "table": "products_cache",
                "mode": "append",
                "auto_create_table": True,
            },
        },
    )
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    finished = await _run_pending(session, "aaj1")
    assert finished.status == RunStatus.SUCCEEDED

    # Sink table really got created on disk.
    out = sqlite3.connect(str(dst_path))
    try:
        cols = [r[1] for r in out.execute('PRAGMA table_info("products_cache")').fetchall()]
    finally:
        out.close()
    assert cols == ["id", "name"]

    # Now walk the REST endpoint a Viewer would call.
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email="aaj1@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get(f"/workspaces/{ws.id}/assets", headers=headers)
        assert resp.status_code == 200, resp.text
        listing = resp.json()
        by_key = {a["asset_key"]: a for a in listing}
        # Source + auto-created sink both registered.
        assert "src/products" in by_key
        assert "dst/products_cache" in by_key

        sink_asset = by_key["dst/products_cache"]
        lineage_resp = await client.get(
            f"/workspaces/{ws.id}/assets/{sink_asset['id']}/lineage",
            headers=headers,
        )
        assert lineage_resp.status_code == 200, lineage_resp.text
        upstream_keys = {a["asset_key"] for a in lineage_resp.json()["upstream"]}
        assert "src/products" in upstream_keys

        # ---- column lineage ----
        # The pipeline's source is a plain ``SELECT id, name FROM
        # products``, so each sink column traces 1:1 back to its source.
        # ``column_lineage_opaque`` must be False (Phase Z/AA/BB) — we
        # don't want auto-created sinks to silently regress to "opaque"
        # just because the runtime made the table on its own.
        col_resp = await client.get(
            f"/workspaces/{ws.id}/assets/{sink_asset['id']}/column-lineage",
            headers=headers,
        )
        assert col_resp.status_code == 200, col_resp.text
        col_body = col_resp.json()
        assert col_body["opaque"] is False
        by_col = {c["name"]: c for c in col_body["columns"]}
        assert {"id", "name"} <= set(by_col)
        for col_name in ("id", "name"):
            ups = {(u["asset_key"], u["column"]) for u in by_col[col_name]["upstreams"]}
            assert ("src/products", col_name) in ups

        # One materialisation entry stamped for the run.
        mat_resp = await client.get(
            f"/workspaces/{ws.id}/assets/{sink_asset['id']}/materializations",
            headers=headers,
        )
        assert mat_resp.status_code == 200, mat_resp.text
        assert len(mat_resp.json()) >= 1
