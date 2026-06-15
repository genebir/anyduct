"""AssetRepository persist + query (ADR-0036, Phase B; ADR-0041 J2). testcontainers."""

from __future__ import annotations

import pytest
from anyduct_server.assets.repository import AssetRepository
from anyduct_server.db.models import Workspace
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.core.asset import AssetKey, AssetLineage, LineageEdge
from etl_plugins.core.column_lineage import (
    ColumnEdge,
    ColumnLineage,
    ColumnRef,
)

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


# --------------- J2: column lineage persistence + query --------------------


async def _seed_assets(session: AsyncSession, ws_id, *keys: AssetKey) -> None:
    """Helper: create asset rows for the keys via persist_run_lineage so the
    column-lineage tests can reference them by key (column lineage runs
    *after* asset lineage in production)."""
    repo = AssetRepository(session)
    for k in keys:
        await repo.persist_run_lineage(
            workspace_id=ws_id,
            run_id=None,
            lineage=AssetLineage(outputs=[k]),
            records_written=0,
            kinds={k: "table"},
        )
    await session.flush()


async def test_persist_column_lineage_creates_columns_and_edges(
    session: AsyncSession,
) -> None:
    ws = await _ws(session, "col-lineage-1")
    src = AssetKey.of("wh", "users")
    dst = AssetKey.of("wh", "customers")
    await _seed_assets(session, ws.id, src, dst)
    repo = AssetRepository(session)

    lineage = ColumnLineage(
        edges=[
            ColumnEdge(
                downstream=ColumnRef(asset=dst, column="id"),
                upstreams=(ColumnRef(asset=src, column="a"),),
            ),
            ColumnEdge(
                downstream=ColumnRef(asset=dst, column="city"),
                upstreams=(ColumnRef(asset=src, column="c"),),
            ),
            ColumnEdge(  # constant / opaque expression — column exists, no upstream
                downstream=ColumnRef(asset=dst, column="tenant"),
            ),
        ]
    )
    await repo.persist_run_column_lineage(workspace_id=ws.id, lineage=lineage, output_keys=[dst])
    await session.flush()

    dst_row = next(
        a for a in await repo.list_for_workspace(workspace_id=ws.id) if str(dst) == a.asset_key
    )
    assert dst_row.column_lineage_opaque is False

    cols, upstream_map = await repo.column_lineage_for_asset(asset_id=dst_row.id)
    assert [c.name for c in cols] == ["city", "id", "tenant"]
    by_name = {c.name: c for c in cols}
    assert [(up.name, ws_asset.asset_key) for up, ws_asset in upstream_map[by_name["id"].id]] == [
        ("a", "wh/users")
    ]
    assert [(up.name, ws_asset.asset_key) for up, ws_asset in upstream_map[by_name["city"].id]] == [
        ("c", "wh/users")
    ]
    assert upstream_map[by_name["tenant"].id] == []


async def test_persist_column_lineage_marks_opaque_asset(session: AsyncSession) -> None:
    ws = await _ws(session, "col-lineage-2")
    dst = AssetKey.of("wh", "blob")
    await _seed_assets(session, ws.id, dst)
    repo = AssetRepository(session)

    await repo.persist_run_column_lineage(
        workspace_id=ws.id,
        lineage=ColumnLineage(opaque_assets=[dst]),
        output_keys=[dst],
    )
    await session.flush()

    dst_row = next(
        a for a in await repo.list_for_workspace(workspace_id=ws.id) if str(dst) == a.asset_key
    )
    assert dst_row.column_lineage_opaque is True
    cols, _ = await repo.column_lineage_for_asset(asset_id=dst_row.id)
    assert cols == []


async def test_persist_column_lineage_replaces_on_rerun(session: AsyncSession) -> None:
    """Each successful run overwrites the output asset's column set + edges
    so the row set reflects the most recent materialization."""
    ws = await _ws(session, "col-lineage-3")
    src = AssetKey.of("wh", "src")
    dst = AssetKey.of("wh", "dst")
    await _seed_assets(session, ws.id, src, dst)
    repo = AssetRepository(session)

    # Run 1: id, name
    await repo.persist_run_column_lineage(
        workspace_id=ws.id,
        lineage=ColumnLineage(
            edges=[
                ColumnEdge(ColumnRef(dst, "id"), (ColumnRef(src, "id"),)),
                ColumnEdge(ColumnRef(dst, "name"), (ColumnRef(src, "name"),)),
            ]
        ),
        output_keys=[dst],
    )
    await session.flush()

    # Run 2: schema changed — drop name, add city
    await repo.persist_run_column_lineage(
        workspace_id=ws.id,
        lineage=ColumnLineage(
            edges=[
                ColumnEdge(ColumnRef(dst, "id"), (ColumnRef(src, "id"),)),
                ColumnEdge(ColumnRef(dst, "city"), (ColumnRef(src, "city"),)),
            ]
        ),
        output_keys=[dst],
    )
    await session.flush()

    dst_row = next(
        a for a in await repo.list_for_workspace(workspace_id=ws.id) if str(dst) == a.asset_key
    )
    cols, _ = await repo.column_lineage_for_asset(asset_id=dst_row.id)
    assert [c.name for c in cols] == ["city", "id"]  # no stale "name"


async def test_persist_column_lineage_flips_opaque_back_off(session: AsyncSession) -> None:
    """An asset that was opaque on run N becomes traceable on run N+1."""
    ws = await _ws(session, "col-lineage-4")
    src = AssetKey.of("wh", "src")
    dst = AssetKey.of("wh", "dst")
    await _seed_assets(session, ws.id, src, dst)
    repo = AssetRepository(session)

    await repo.persist_run_column_lineage(
        workspace_id=ws.id,
        lineage=ColumnLineage(opaque_assets=[dst]),
        output_keys=[dst],
    )
    await session.flush()

    await repo.persist_run_column_lineage(
        workspace_id=ws.id,
        lineage=ColumnLineage(edges=[ColumnEdge(ColumnRef(dst, "id"), (ColumnRef(src, "id"),))]),
        output_keys=[dst],
    )
    await session.flush()

    dst_row = next(
        a for a in await repo.list_for_workspace(workspace_id=ws.id) if str(dst) == a.asset_key
    )
    assert dst_row.column_lineage_opaque is False
    cols, _ = await repo.column_lineage_for_asset(asset_id=dst_row.id)
    assert [c.name for c in cols] == ["id"]


async def test_persist_column_lineage_n_to_one_join_shape(session: AsyncSession) -> None:
    """Multi-upstream (join-like) downstream column persists as n rows."""
    ws = await _ws(session, "col-lineage-5")
    src_a = AssetKey.of("wh", "a")
    src_b = AssetKey.of("wh", "b")
    dst = AssetKey.of("wh", "joined")
    await _seed_assets(session, ws.id, src_a, src_b, dst)
    repo = AssetRepository(session)

    await repo.persist_run_column_lineage(
        workspace_id=ws.id,
        lineage=ColumnLineage(
            edges=[
                ColumnEdge(
                    ColumnRef(dst, "merged"),
                    (ColumnRef(src_a, "x"), ColumnRef(src_b, "y")),
                ),
            ]
        ),
        output_keys=[dst],
    )
    await session.flush()

    dst_row = next(
        a for a in await repo.list_for_workspace(workspace_id=ws.id) if str(dst) == a.asset_key
    )
    cols, ups = await repo.column_lineage_for_asset(asset_id=dst_row.id)
    assert [c.name for c in cols] == ["merged"]
    merged = cols[0]
    refs = [(up.name, ws_asset.asset_key) for up, ws_asset in ups[merged.id]]
    assert sorted(refs) == [("x", "wh/a"), ("y", "wh/b")]
