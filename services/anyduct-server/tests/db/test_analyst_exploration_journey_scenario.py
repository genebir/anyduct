"""Analyst catalog exploration journey (Phase UU, 2026-05-29).

User persona: a data analyst who didn't build the pipelines but needs to
understand *what's in the catalog right now*. They have ``Viewer`` role
in a workspace where an Owner has already set up + run a 2-stage chain
(``raw → staging → mart``). Their daily flow is:

1. ``GET /workspaces/{ws}/assets`` — what assets are there?
2. ``GET /workspaces/{ws}/assets/{id}/lineage`` — where did this asset
   come from? what consumes it?
3. ``GET /workspaces/{ws}/assets/{id}/column-lineage`` — at the column
   level, where did ``mart.amount`` originate?
4. ``GET /workspaces/{ws}/assets/{id}/materializations`` — when was this
   last refreshed? how many rows?
5. The analyst also tries an *Owner-only* action and gets a clean 403
   (no accidental access escalation).

The first four steps must work seamlessly on a Viewer token — that's
the whole point of having a separate role. The fifth checks the
boundary: read-only means read-only.
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
from anyduct_server.db.enums import AuthMethod, WorkspaceRole
from anyduct_server.db.models import Membership, User, Workspace
from anyduct_server.dependencies import get_session
from anyduct_server.settings import Settings
from anyduct_server.worker.claim import claim_pending_run
from anyduct_server.worker.executor import RunExecutor
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


# ----- harness ---------------------------------------------------------------


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
    app.state.secret_backend = StaticSecretBackend()
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        email=email.lower(),
        name="Analyst",
        auth_method=AuthMethod.LOCAL,
        password_hash=PasswordService(rounds=4).hash("hunter2"),  # pragma: allowlist secret
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_workspace_with_member(
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


async def _drain_pending_runs(session: AsyncSession, worker_id: str) -> int:
    executor = RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    )
    executed = 0
    while True:
        claimed = await claim_pending_run(session, worker_id=worker_id)
        if claimed is None:
            break
        await session.commit()
        await executor.execute(claimed.id)
        executed += 1
    return executed


# ===== UU1: Analyst explores a populated catalog ===========================


async def test_uu1_analyst_walks_catalog_to_trace_a_column(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Set up the catalog the way an Owner would: 2-stage chain
    ``raw → staging → mart``. Then login as a Viewer and walk the
    catalog as an analyst would, drilling down to the column origin of
    ``mart.amount`` and confirming the last materialization. Finally
    try a forbidden action and assert a clean 403."""
    # --- Owner side: seed warehouse + run the chain so the catalog is full ---
    db_path = tmp_path / "uu.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, amount INTEGER)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, 100), (2, 250), (3, 75)])
        conn.execute("CREATE TABLE staging (id INTEGER, amount INTEGER)")
        conn.execute("CREATE TABLE mart (id INTEGER, amount INTEGER)")
        conn.commit()
    finally:
        conn.close()

    analyst = await _seed_user(session, email="analyst@example.com")
    ws = await _seed_workspace_with_member(
        session, slug="uu-ws", user=analyst, role=WorkspaceRole.VIEWER
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    a_cfg = {
        "name": "A",
        "source": {"connection": "src", "query": "SELECT id, amount FROM raw"},
        "sink": {"connection": "dst", "table": "staging", "mode": "append"},
    }
    b_cfg = {
        "name": "B",
        "source": {"connection": "dst", "query": "SELECT id, amount FROM staging"},
        "sink": {"connection": "dst", "table": "mart", "mode": "append"},
    }
    p_a, pv_a = await _seed_pipeline(session, workspace_id=ws.id, name="A", config=a_cfg)
    p_b, pv_b = await _seed_pipeline(session, workspace_id=ws.id, name="B", config=b_cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p_a.id, pipeline_version_id=pv_a.id
    )
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p_b.id, pipeline_version_id=pv_b.id
    )
    await _drain_pending_runs(session, "uu-init")

    # --- Analyst side: walk the catalog ---
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=analyst.email)
        h = {"Authorization": f"Bearer {token}"}

        # Step 1: what's in the catalog?
        list_resp = await client.get(f"/workspaces/{ws.id}/assets", headers=h)
        assert list_resp.status_code == 200, list_resp.text
        listing = {a["asset_key"]: a for a in list_resp.json()}
        # The analyst expects every layer to be discoverable. Note B
        # points its source at the ``dst`` connection (that's where A
        # wrote staging), so the read-side key is also under ``dst``.
        for key in ("src/raw", "dst/staging", "dst/mart"):
            assert key in listing, f"analyst can't see {key}: {sorted(listing)}"

        mart = listing["dst/mart"]
        # ``column_lineage_opaque`` is the badge an analyst checks first —
        # "is this asset traceable, or did the worker punt?". Phase X/Z/AA
        # promise it's False for SQL chains like this.
        assert mart["column_lineage_opaque"] is False

        # Step 2: where did ``mart`` come from? What consumes it?
        lin_resp = await client.get(f"/workspaces/{ws.id}/assets/{mart['id']}/lineage", headers=h)
        assert lin_resp.status_code == 200
        lineage = lin_resp.json()
        # The analyst expects to see the immediate upstream + downstream
        # so they can navigate "one click up, one click down".
        upstream_keys = {a["asset_key"] for a in lineage["upstream"]}
        assert "dst/staging" in upstream_keys
        # Nothing further consumes ``mart`` in this chain.
        assert lineage["downstream"] == []

        # Step 3: column drill-down — where does mart.amount come from?
        col_resp = await client.get(
            f"/workspaces/{ws.id}/assets/{mart['id']}/column-lineage", headers=h
        )
        assert col_resp.status_code == 200
        col_body = col_resp.json()
        assert col_body["opaque"] is False
        by_col = {c["name"]: c for c in col_body["columns"]}
        assert "amount" in by_col
        # The UI lets the analyst keep climbing — each upstream ref carries
        # both ``asset_key`` (for the lineage panel) and ``column`` (for
        # highlighting in the next view).
        amount_ups = by_col["amount"]["upstreams"]
        keys = {(u["asset_key"], u["column"]) for u in amount_ups}
        assert ("dst/staging", "amount") in keys

        # Step 4: when was ``mart`` last refreshed? how many rows?
        mat_resp = await client.get(
            f"/workspaces/{ws.id}/assets/{mart['id']}/materializations", headers=h
        )
        assert mat_resp.status_code == 200
        mats = mat_resp.json()
        # Single run so far, but the audit row is the basis of the
        # "last refreshed" widget in the UI.
        assert len(mats) == 1
        assert mats[0]["records_written"] == 3

        # Step 5: the analyst tries a Viewer-forbidden action — creating a
        # new pipeline. They get 403, not 401, and not a generic 500. The
        # UI uses this to decide "show the disabled state vs the locked
        # state" toast.
        forbidden = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            headers=h,
            json={
                "name": "analyst_can_not_create",
                "config": {
                    "source": {"connection": "src", "query": "SELECT 1 AS x"},
                    "sink": {"connection": "dst", "table": "x", "mode": "append"},
                },
            },
        )
        assert forbidden.status_code == 403, forbidden.text
