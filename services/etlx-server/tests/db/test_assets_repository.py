"""AssetRepository persist + query (ADR-0036, Phase B). testcontainers."""

from __future__ import annotations

import pytest
from etlx_server.assets.repository import AssetRepository
from etlx_server.db.models import Workspace
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.core.asset import AssetKey, AssetLineage, LineageEdge

pytestmark = pytest.mark.asyncio


async def _ws(session: AsyncSession, slug: str) -> Workspace:
    ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
    session.add(ws)
    await session.flush()
    return ws


def _lin(inp: AssetKey, out: AssetKey) -> AssetLineage:
    return AssetLineage(inputs=[inp], outputs=[out], edges=[LineageEdge(inp, out)])


async def test_persist_creates_assets_edges_materialization(session: AsyncSession) -> None:
    ws = await _ws(session, "lineage-1")
    repo = AssetRepository(session)
    src = AssetKey.of("src", "public.orders")
    dst = AssetKey.of("dst", "public.orders_copy")

    await repo.persist_run_lineage(
        workspace_id=ws.id,
        run_id=None,
        lineage=_lin(src, dst),
        records_written=42,
        kinds={src: "table", dst: "table"},
    )
    await session.flush()

    assets = await repo.list_for_workspace(workspace_id=ws.id)
    keys = {a.asset_key for a in assets}
    assert keys == {"src/public.orders", "dst/public.orders_copy"}

    dst_row = next(a for a in assets if a.asset_key == "dst/public.orders_copy")
    assert dst_row.kind == "table"
    assert dst_row.last_materialized_at is not None

    ups = await repo.upstream(dst_row.id)
    assert [a.asset_key for a in ups] == ["src/public.orders"]

    mats = await repo.materializations(asset_id=dst_row.id)
    assert len(mats) == 1
    assert mats[0].records_written == 42


async def test_persist_is_idempotent_assets_and_edges(session: AsyncSession) -> None:
    ws = await _ws(session, "lineage-2")
    repo = AssetRepository(session)
    src = AssetKey.of("src", "t1")
    dst = AssetKey.of("dst", "t2")

    for _ in range(3):
        await repo.persist_run_lineage(
            workspace_id=ws.id, run_id=None, lineage=_lin(src, dst), records_written=1
        )
        await session.flush()

    assets = await repo.list_for_workspace(workspace_id=ws.id)
    assert len(assets) == 2  # not duplicated across 3 runs
    dst_row = next(a for a in assets if a.asset_key == "dst/t2")
    # 3 runs → 3 materializations, but still exactly 1 edge.
    mats = await repo.materializations(asset_id=dst_row.id)
    assert len(mats) == 3
    ups = await repo.upstream(dst_row.id)
    assert len(ups) == 1


async def test_cross_pipeline_lineage_links_via_shared_key(session: AsyncSession) -> None:
    ws = await _ws(session, "lineage-3")
    repo = AssetRepository(session)
    raw = AssetKey.of("lake", "raw")
    staged = AssetKey.of("wh", "staged")
    mart = AssetKey.of("wh", "mart")

    await repo.persist_run_lineage(
        workspace_id=ws.id, run_id=None, lineage=_lin(raw, staged), records_written=1
    )
    await repo.persist_run_lineage(
        workspace_id=ws.id, run_id=None, lineage=_lin(staged, mart), records_written=1
    )
    await session.flush()

    assets = {a.asset_key: a for a in await repo.list_for_workspace(workspace_id=ws.id)}
    staged_row = assets["wh/staged"]
    # staged is downstream of raw AND upstream of mart — the two runs linked it.
    assert [a.asset_key for a in await repo.upstream(staged_row.id)] == ["lake/raw"]
    assert [a.asset_key for a in await repo.downstream(staged_row.id)] == ["wh/mart"]


async def test_workspace_scoped(session: AsyncSession) -> None:
    ws_a = await _ws(session, "lineage-a")
    ws_b = await _ws(session, "lineage-b")
    repo = AssetRepository(session)
    k = AssetKey.of("conn", "t")
    await repo.persist_run_lineage(
        workspace_id=ws_a.id, run_id=None, lineage=AssetLineage(outputs=[k]), records_written=1
    )
    await session.flush()
    assert len(await repo.list_for_workspace(workspace_id=ws_a.id)) == 1
    assert len(await repo.list_for_workspace(workspace_id=ws_b.id)) == 0
