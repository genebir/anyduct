"""Catalog REST endpoints — assets + lineage (ADR-0036, Phase B3; ADR-0041 J2). testcontainers."""

from __future__ import annotations

from collections.abc import AsyncIterator

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
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.core.asset import AssetKey, AssetLineage, LineageEdge
from etl_plugins.core.column_lineage import ColumnEdge, ColumnLineage, ColumnRef

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
        password_hash=PasswordService(rounds=4).hash("hunter2"),
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_ws(
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


async def _seed_lineage(session: AsyncSession, ws_id) -> None:
    repo = AssetRepository(session)
    raw = AssetKey.of("lake", "raw.events")
    staged = AssetKey.of("wh", "staging.events")
    await repo.persist_run_lineage(
        workspace_id=ws_id,
        run_id=None,
        lineage=AssetLineage(inputs=[raw], outputs=[staged], edges=[LineageEdge(raw, staged)]),
        records_written=7,
        kinds={raw: "table", staged: "table"},
    )
    await session.flush()


async def test_list_lineage_and_materializations(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="cat-owner@example.com")
    ws = await _seed_ws(session, slug="cat-1", user=owner, role=WorkspaceRole.VIEWER)
    await _seed_lineage(session, ws.id)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        h = {"Authorization": f"Bearer {token}"}
        ls = await client.get(f"/workspaces/{ws.id}/assets", headers=h)
        assert ls.status_code == 200, ls.text
        rows = ls.json()
        keys = {r["asset_key"] for r in rows}
        assert keys == {"lake/raw.events", "wh/staging.events"}

        staged = next(r for r in rows if r["asset_key"] == "wh/staging.events")
        assert staged["kind"] == "table"
        assert staged["last_materialized_at"] is not None

        lin = await client.get(f"/workspaces/{ws.id}/assets/{staged['id']}/lineage", headers=h)
        assert lin.status_code == 200, lin.text
        assert [u["asset_key"] for u in lin.json()["upstream"]] == ["lake/raw.events"]

        mats = await client.get(
            f"/workspaces/{ws.id}/assets/{staged['id']}/materializations", headers=h
        )
        assert mats.status_code == 200, mats.text
        assert len(mats.json()) == 1
        assert mats.json()[0]["records_written"] == 7


async def test_assets_non_member_forbidden(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="cat-owner2@example.com")
    outsider = await _seed_user(session, email="cat-out@example.com")
    ws = await _seed_ws(session, slug="cat-2", user=owner, role=WorkspaceRole.OWNER)
    await _seed_lineage(session, ws.id)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=outsider.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/assets", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 403


async def test_asset_lineage_404_for_unknown_id(session: AsyncSession) -> None:
    from uuid import uuid4

    owner = await _seed_user(session, email="cat-404@example.com")
    ws = await _seed_ws(session, slug="cat-3", user=owner, role=WorkspaceRole.VIEWER)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/assets/{uuid4()}/lineage",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


# --------------- J2: column lineage endpoint ------------------------------


async def _seed_column_lineage(session: AsyncSession, ws_id) -> tuple[str, str]:
    """Returns (src_key, dst_key) — same shape as the J1 example: rename +
    add_constant + opaque expression."""
    repo = AssetRepository(session)
    src = AssetKey.of("wh", "users")
    dst = AssetKey.of("wh", "customers")
    # Asset rows (column lineage runs after asset lineage in production).
    await repo.persist_run_lineage(
        workspace_id=ws_id,
        run_id=None,
        lineage=AssetLineage(inputs=[src], outputs=[dst], edges=[LineageEdge(src, dst)]),
        records_written=0,
        kinds={src: "table", dst: "table"},
    )
    await repo.persist_run_column_lineage(
        workspace_id=ws_id,
        lineage=ColumnLineage(
            edges=[
                ColumnEdge(ColumnRef(dst, "id"), (ColumnRef(src, "a"),)),
                ColumnEdge(ColumnRef(dst, "city"), (ColumnRef(src, "c"),)),
                ColumnEdge(ColumnRef(dst, "tenant")),  # add_constant → no upstream
            ]
        ),
        output_keys=[dst],
    )
    await session.flush()
    return str(src), str(dst)


async def test_column_lineage_endpoint_returns_columns_and_upstreams(
    session: AsyncSession,
) -> None:
    owner = await _seed_user(session, email="col-owner@example.com")
    ws = await _seed_ws(session, slug="col-1", user=owner, role=WorkspaceRole.VIEWER)
    _src_key, dst_key = await _seed_column_lineage(session, ws.id)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        h = {"Authorization": f"Bearer {token}"}
        ls = await client.get(f"/workspaces/{ws.id}/assets", headers=h)
        dst = next(r for r in ls.json() if r["asset_key"] == dst_key)

        resp = await client.get(f"/workspaces/{ws.id}/assets/{dst['id']}/column-lineage", headers=h)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["asset_key"] == dst_key
        assert body["opaque"] is False
        names = [c["name"] for c in body["columns"]]
        assert names == ["city", "id", "tenant"]  # alphabetical
        by_name = {c["name"]: c for c in body["columns"]}
        assert [(u["asset_key"], u["column"]) for u in by_name["id"]["upstreams"]] == [
            ("wh/users", "a")
        ]
        assert by_name["tenant"]["upstreams"] == []  # constant column


async def test_column_lineage_endpoint_opaque_asset(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="col-opaque@example.com")
    ws = await _seed_ws(session, slug="col-2", user=owner, role=WorkspaceRole.VIEWER)
    repo = AssetRepository(session)
    dst = AssetKey.of("wh", "blob")
    await repo.persist_run_lineage(
        workspace_id=ws.id,
        run_id=None,
        lineage=AssetLineage(outputs=[dst]),
        records_written=0,
        kinds={dst: "table"},
    )
    await repo.persist_run_column_lineage(
        workspace_id=ws.id,
        lineage=ColumnLineage(opaque_assets=[dst]),
        output_keys=[dst],
    )
    await session.flush()
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        h = {"Authorization": f"Bearer {token}"}
        ls = await client.get(f"/workspaces/{ws.id}/assets", headers=h)
        blob = next(r for r in ls.json() if r["asset_key"] == "wh/blob")
        resp = await client.get(
            f"/workspaces/{ws.id}/assets/{blob['id']}/column-lineage", headers=h
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["opaque"] is True
        assert body["columns"] == []


async def test_column_lineage_endpoint_non_member_forbidden(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="col-owner-3@example.com")
    outsider = await _seed_user(session, email="col-out@example.com")
    ws = await _seed_ws(session, slug="col-3", user=owner, role=WorkspaceRole.OWNER)
    _src_key, dst_key = await _seed_column_lineage(session, ws.id)
    app = _build_app(session)
    async with _client(app) as client:
        owner_tok = await _login(client, email=owner.email)
        ls = await client.get(
            f"/workspaces/{ws.id}/assets", headers={"Authorization": f"Bearer {owner_tok}"}
        )
        dst = next(r for r in ls.json() if r["asset_key"] == dst_key)
        out_tok = await _login(client, email=outsider.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/assets/{dst['id']}/column-lineage",
            headers={"Authorization": f"Bearer {out_tok}"},
        )
    assert resp.status_code == 403


# --------------- multi-hop column-lineage graph (2026-06-12) ---------------


async def _seed_two_hop_column_lineage(session: AsyncSession, ws_id) -> str:
    """raw → staging → mart with column lineage at both hops; returns mart key."""
    repo = AssetRepository(session)
    raw = AssetKey.of("wh", "raw")
    stg = AssetKey.of("wh", "staging")
    mart = AssetKey.of("wh", "mart")
    for src, dst in ((raw, stg), (stg, mart)):
        await repo.persist_run_lineage(
            workspace_id=ws_id,
            run_id=None,
            lineage=AssetLineage(inputs=[src], outputs=[dst], edges=[LineageEdge(src, dst)]),
            records_written=0,
            kinds={src: "table", dst: "table"},
        )
    await repo.persist_run_column_lineage(
        workspace_id=ws_id,
        lineage=ColumnLineage(
            edges=[
                ColumnEdge(ColumnRef(stg, "id"), (ColumnRef(raw, "id"),)),
                ColumnEdge(ColumnRef(stg, "amount"), (ColumnRef(raw, "amt"),)),
            ]
        ),
        output_keys=[stg],
    )
    await repo.persist_run_column_lineage(
        workspace_id=ws_id,
        lineage=ColumnLineage(
            edges=[
                ColumnEdge(ColumnRef(mart, "id"), (ColumnRef(stg, "id"),)),
                ColumnEdge(ColumnRef(mart, "total"), (ColumnRef(stg, "amount"),)),
            ]
        ),
        output_keys=[mart],
    )
    await session.flush()
    return str(mart)


async def test_column_lineage_graph_walks_two_hops(session: AsyncSession) -> None:
    """The conventional drill-down: from mart, depth=3 returns the whole
    raw → staging → mart chain with per-lane depths and all 4 edges."""
    owner = await _seed_user(session, email="clg-owner@example.com")
    ws = await _seed_ws(session, slug="clg-1", user=owner, role=WorkspaceRole.VIEWER)
    mart_key = await _seed_two_hop_column_lineage(session, ws.id)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        h = {"Authorization": f"Bearer {token}"}
        ls = await client.get(f"/workspaces/{ws.id}/assets", headers=h)
        mart = next(r for r in ls.json() if r["asset_key"] == mart_key)

        resp = await client.get(
            f"/workspaces/{ws.id}/assets/{mart['id']}/column-lineage-graph?depth=3",
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["asset_key"] == mart_key
        assert body["truncated"] is False
        depth_by_key = {a["asset_key"]: a["depth"] for a in body["assets"]}
        assert depth_by_key == {"wh/mart": 0, "wh/staging": 1, "wh/raw": 2}
        cols_by_key = {a["asset_key"]: a["columns"] for a in body["assets"]}
        assert cols_by_key["wh/mart"] == ["id", "total"]
        assert cols_by_key["wh/staging"] == ["amount", "id"]
        assert cols_by_key["wh/raw"] == ["amt", "id"]
        key_by_id = {a["id"]: a["asset_key"] for a in body["assets"]}
        edges = {
            (
                key_by_id[e["from_asset_id"]],
                e["from_column"],
                key_by_id[e["to_asset_id"]],
                e["to_column"],
            )
            for e in body["edges"]
        }
        assert edges == {
            ("wh/raw", "id", "wh/staging", "id"),
            ("wh/raw", "amt", "wh/staging", "amount"),
            ("wh/staging", "id", "wh/mart", "id"),
            ("wh/staging", "amount", "wh/mart", "total"),
        }


async def test_column_lineage_graph_depth_cap_reports_truncated(session: AsyncSession) -> None:
    """depth=1 stops at staging; the probe sees raw feeding it → truncated."""
    owner = await _seed_user(session, email="clg-cap@example.com")
    ws = await _seed_ws(session, slug="clg-2", user=owner, role=WorkspaceRole.VIEWER)
    mart_key = await _seed_two_hop_column_lineage(session, ws.id)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        h = {"Authorization": f"Bearer {token}"}
        ls = await client.get(f"/workspaces/{ws.id}/assets", headers=h)
        mart = next(r for r in ls.json() if r["asset_key"] == mart_key)

        resp = await client.get(
            f"/workspaces/{ws.id}/assets/{mart['id']}/column-lineage-graph?depth=1",
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["truncated"] is True
        assert {a["asset_key"] for a in body["assets"]} == {"wh/mart", "wh/staging"}
        assert len(body["edges"]) == 2
