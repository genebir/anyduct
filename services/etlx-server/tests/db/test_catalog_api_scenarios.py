"""Catalog REST API dogfooding scenarios (Phase KK, 2026-05-29).

The UI's "click-through asset lineage" experience hits four REST
endpoints in sequence:

* ``GET /workspaces/{ws}/assets`` — list.
* ``GET /workspaces/{ws}/assets/{id}/lineage`` — upstream/downstream refs.
* ``GET /workspaces/{ws}/assets/{id}/materializations`` — audit trail.
* ``GET /workspaces/{ws}/assets/{id}/column-lineage`` — column drill-down.

Existing router tests seed the catalog directly via ``AssetRepository``;
this module instead seeds it through a *real pipeline run* with sample
data, then walks the same four endpoints the UI does. That catches
serialization mismatches and shape drift between what the worker writes
and what the API hands the browser.

Scenarios:

* **KK1** — A 2-pipeline chain (raw → staging → mart) creates 3 catalog
  rows; the REST list + lineage endpoints return them with the expected
  upstream / downstream linkage.
* **KK2** — Multiple runs of the same pipeline materialise the same
  asset row but accumulate two materialisation entries; column-lineage
  endpoint returns one upstream per traceable column.
* **KK3** — Cross-workspace 404 (an asset id from ws_b returns 404 when
  fetched through ws_a's URL) + non-member 403.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from etlx_server.app_factory import create_app
from etlx_server.assets.repository import AssetRepository
from etlx_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from etlx_server.auth.password_service import PasswordService
from etlx_server.db.enums import AuthMethod, WorkspaceRole
from etlx_server.db.models import Membership, User, Workspace
from etlx_server.dependencies import get_session
from etlx_server.settings import Settings
from etlx_server.worker.claim import claim_pending_run
from etlx_server.worker.executor import RunExecutor
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import StaticSecretBackend
from tests.db.test_worker_lifecycle import (
    _seed_connection,
    _seed_pending_run,
    _seed_pipeline,
    _SessionFactoryAdapter,
)

pytestmark = pytest.mark.asyncio


# ----- app + http harness ----------------------------------------------------


def _build_app(session: AsyncSession) -> FastAPI:
    """Same pattern as test_assets_router._build_app — overrides
    ``get_session`` so the request handler shares the test transaction
    with whatever the worker just wrote."""
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


async def _run_pending(session: AsyncSession, worker_id: str) -> None:
    claimed = await claim_pending_run(session, worker_id=worker_id)
    assert claimed is not None
    await session.commit()
    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    ).execute(claimed.id)


def _seed_chain_warehouse(tmp_path: Path) -> Path:
    db_path = tmp_path / "kk.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, name TEXT)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, "alice"), (2, "bob")])
        conn.execute("CREATE TABLE staging (id INTEGER, name TEXT)")
        conn.execute("CREATE TABLE mart (id INTEGER, name TEXT)")
        conn.commit()
    finally:
        conn.close()
    return db_path


# ===== KK1: list + lineage REST endpoints ===================================


async def test_kk1_catalog_list_and_lineage_reflect_pipeline_run(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Run a 2-pipeline chain that produces three catalog rows
    (``src/raw`` → ``dst/staging`` → ``dst/mart``). Then walk the REST
    list endpoint + lineage endpoint and assert each response matches
    what the catalog actually holds. This catches API-shape drift that
    unit tests against the repository would miss."""
    db_path = _seed_chain_warehouse(tmp_path)
    user = await _seed_user(session, email="kk1@example.com")
    ws = await _seed_ws_with_member(session, slug="kk1", user=user, role=WorkspaceRole.VIEWER)
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    p_a, pv_a = await _seed_pipeline(
        session,
        workspace_id=ws.id,
        name="A",
        config={
            "name": "A",
            "source": {"connection": "src", "query": "SELECT id, name FROM raw"},
            "sink": {"connection": "dst", "table": "staging", "mode": "append"},
        },
    )
    p_b, pv_b = await _seed_pipeline(
        session,
        workspace_id=ws.id,
        name="B",
        config={
            "name": "B",
            "source": {"connection": "dst", "query": "SELECT id, name FROM staging"},
            "sink": {"connection": "dst", "table": "mart", "mode": "append"},
        },
    )
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p_a.id, pipeline_version_id=pv_a.id
    )
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p_b.id, pipeline_version_id=pv_b.id
    )
    await _run_pending(session, "kk1-a")
    await _run_pending(session, "kk1-b")

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        headers = {"Authorization": f"Bearer {token}"}

        # ---- list ----
        resp = await client.get(f"/workspaces/{ws.id}/assets", headers=headers)
        assert resp.status_code == 200, resp.text
        listing = resp.json()
        by_key = {a["asset_key"]: a for a in listing}
        assert set(by_key) == {"src/raw", "dst/staging", "dst/mart"}

        # ---- lineage on the middle node ----
        staging_id = by_key["dst/staging"]["id"]
        lin_resp = await client.get(
            f"/workspaces/{ws.id}/assets/{staging_id}/lineage", headers=headers
        )
        assert lin_resp.status_code == 200
        lineage = lin_resp.json()
        # staging is downstream of raw, upstream of mart.
        upstream_keys = {a["asset_key"] for a in lineage["upstream"]}
        downstream_keys = {a["asset_key"] for a in lineage["downstream"]}
        assert upstream_keys == {"src/raw"}
        assert downstream_keys == {"dst/mart"}


# ===== KK2: materializations + column-lineage REST endpoints ================


async def test_kk2_materializations_and_column_lineage_via_rest(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Run the same pipeline twice, then walk the materializations +
    column-lineage endpoints. We expect two materialisation entries
    (audit trail) and per-column upstream refs traceable to ``src/raw``."""
    db_path = _seed_chain_warehouse(tmp_path)
    user = await _seed_user(session, email="kk2@example.com")
    ws = await _seed_ws_with_member(session, slug="kk2", user=user, role=WorkspaceRole.VIEWER)
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    p, pv = await _seed_pipeline(
        session,
        workspace_id=ws.id,
        name="p",
        config={
            "name": "p",
            "source": {"connection": "src", "query": "SELECT id, name FROM raw"},
            "sink": {"connection": "dst", "table": "staging", "mode": "append"},
        },
    )
    # Two runs.
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await _run_pending(session, "kk2-1")
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await _run_pending(session, "kk2-2")

    repo = AssetRepository(session)
    staging = next(
        a for a in await repo.list_for_workspace(workspace_id=ws.id) if a.asset_key == "dst/staging"
    )

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        headers = {"Authorization": f"Bearer {token}"}

        # ---- materializations ----
        mat_resp = await client.get(
            f"/workspaces/{ws.id}/assets/{staging.id}/materializations",
            headers=headers,
        )
        assert mat_resp.status_code == 200
        entries = mat_resp.json()
        assert len(entries) == 2, "two runs → two materialisations (audit trail)"

        # ---- column lineage ----
        col_resp = await client.get(
            f"/workspaces/{ws.id}/assets/{staging.id}/column-lineage",
            headers=headers,
        )
        assert col_resp.status_code == 200
        body = col_resp.json()
        assert body["opaque"] is False
        by_col = {c["name"]: c for c in body["columns"]}
        assert {"id", "name"} <= set(by_col)
        for col_name in ("id", "name"):
            ups = by_col[col_name]["upstreams"]
            keys = {(u["asset_key"], u["column"]) for u in ups}
            assert ("src/raw", col_name) in keys


# ===== KK3: cross-workspace 404 + non-member 403 ============================


async def test_kk3_cross_workspace_returns_404_and_non_member_403(
    session: AsyncSession, tmp_path: Path
) -> None:
    """ws_a has a member ``u_a`` and a catalog row. ws_b has a different
    member ``u_b`` and a *different* catalog row.

    * ``u_a`` fetching ws_b's asset via the ws_a URL must 404 (resource
      not in that workspace).
    * ``u_b`` who isn't a member of ws_a must get 403 when fetching ws_a's
      asset list (workspace context guard fires before the resource).
    """
    # Per-ws subdirs so the sqlite files live under writeable paths;
    # ``_seed_chain_warehouse`` expects an existing directory.
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    db_a = _seed_chain_warehouse(tmp_path / "a")
    db_b = _seed_chain_warehouse(tmp_path / "b")

    u_a = await _seed_user(session, email="kk3-a@example.com")
    u_b = await _seed_user(session, email="kk3-b@example.com")
    ws_a = await _seed_ws_with_member(session, slug="kk3-a", user=u_a, role=WorkspaceRole.VIEWER)
    ws_b = await _seed_ws_with_member(session, slug="kk3-b", user=u_b, role=WorkspaceRole.VIEWER)

    for ws, db_path in ((ws_a, db_a), (ws_b, db_b)):
        await _seed_connection(
            session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
        )
        await _seed_connection(
            session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
        )
        p, pv = await _seed_pipeline(
            session,
            workspace_id=ws.id,
            name="p",
            config={
                "name": "p",
                "source": {"connection": "src", "query": "SELECT id, name FROM raw"},
                "sink": {"connection": "dst", "table": "staging", "mode": "append"},
            },
        )
        await _seed_pending_run(
            session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
        )
        await _run_pending(session, f"kk3-{ws.slug}")

    # Find the staging asset rows on each side.
    repo = AssetRepository(session)
    a_assets = await repo.list_for_workspace(workspace_id=ws_a.id)
    b_assets = await repo.list_for_workspace(workspace_id=ws_b.id)
    a_staging = next(a for a in a_assets if a.asset_key == "dst/staging")
    b_staging = next(a for a in b_assets if a.asset_key == "dst/staging")

    app = _build_app(session)
    async with _client(app) as client:
        # u_a's token, fetching ws_b's asset id via ws_a URL → 404.
        token_a = await _login(client, email=u_a.email)
        resp = await client.get(
            f"/workspaces/{ws_a.id}/assets/{b_staging.id}/lineage",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 404, resp.text

        # u_b's token, fetching ws_a's asset list at all → 403 (non-member).
        token_b = await _login(client, email=u_b.email)
        resp_403 = await client.get(
            f"/workspaces/{ws_a.id}/assets",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_403.status_code == 403, resp_403.text

        # Sanity: u_a *can* read their own staging asset.
        ok = await client.get(
            f"/workspaces/{ws_a.id}/assets/{a_staging.id}/lineage",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert ok.status_code == 200
