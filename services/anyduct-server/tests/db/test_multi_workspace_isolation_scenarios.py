"""Multi-workspace isolation dogfooding scenarios (Phase HH, 2026-05-29).

The auto-materialize trigger and the catalog repository both filter by
``workspace_id``; what we haven't proven end-to-end is that two
workspaces using the *same connection name* and the *same table name*
(a realistic multi-tenant scenario — every customer's "src" connection,
every customer's "orders" table) get the boundary they expect.

Two scenarios, each with sample data on two separate sqlite files:

* **HH1** — auto-materialize stays inside the workspace. Workspace A and
  Workspace B each have a producer + an opt-in consumer reading the same
  asset key. Triggering A's producer must trigger only A's consumer.
* **HH2** — ``list_for_workspace`` returns only its workspace's rows
  even when the asset_key string is identical across workspaces.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

import pytest
from anyduct_server.assets.repository import AssetRepository
from anyduct_server.db.enums import RunStatus
from anyduct_server.db.models import Run
from anyduct_server.worker.claim import claim_pending_run
from anyduct_server.worker.executor import RunExecutor
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import StaticSecretBackend
from tests.db.test_worker_lifecycle import (
    _seed_connection,
    _seed_pending_run,
    _seed_pipeline,
    _seed_workspace,
    _SessionFactoryAdapter,
)

pytestmark = pytest.mark.asyncio


# ----- helpers ---------------------------------------------------------------


async def _drain_pending_runs(session: AsyncSession, worker_id: str) -> int:
    """Drain every PENDING run (chained ones included) for the whole DB."""
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


def _seed_two_ws_warehouse(tmp_path: Path) -> tuple[Path, Path]:
    """Two independent sqlite files mirroring the same schema (raw/staging/
    mart). Same table names — only the workspace_id differs, which is
    exactly the multi-tenant shape we want to stress."""
    paths = []
    for name in ("ws_a", "ws_b"):
        db_path = tmp_path / f"{name}.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("CREATE TABLE raw (id INTEGER, amount INTEGER)")
            conn.executemany(
                "INSERT INTO raw VALUES (?, ?)",
                # Different sample rows per workspace so we can prove each
                # consumer worked on its own data.
                ([(1, 10), (2, 20)] if name == "ws_a" else [(11, 110), (12, 120)]),
            )
            conn.execute("CREATE TABLE staging (id INTEGER, amount INTEGER)")
            conn.execute("CREATE TABLE mart (id INTEGER, amount INTEGER)")
            conn.commit()
        finally:
            conn.close()
        paths.append(db_path)
    return paths[0], paths[1]


async def _runs_for_pipeline(session: AsyncSession, pipeline_id: UUID) -> list[Run]:
    await session.commit()
    rows = await session.execute(
        select(Run).where(Run.pipeline_id == pipeline_id).order_by(Run.created_at)
    )
    return list(rows.scalars().all())


# ===== HH1: Auto-materialize is workspace-scoped ============================


async def test_hh1_auto_materialize_does_not_cross_workspace_boundary(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Two workspaces, each with the same producer / consumer shape +
    same asset key strings. Triggering A's producer must enqueue only
    A's consumer; B's consumer must stay idle."""
    db_a, db_b = _seed_two_ws_warehouse(tmp_path)

    ws_a = await _seed_workspace(session, slug="hh1-a")
    ws_b = await _seed_workspace(session, slug="hh1-b")
    # Both workspaces use the *same* connection names + same table names —
    # only the connection's `database` path differs (per-tenant data file).
    await _seed_connection(
        session, workspace_id=ws_a.id, name="src", config={"database": str(db_a)}
    )
    await _seed_connection(
        session, workspace_id=ws_a.id, name="dst", config={"database": str(db_a)}
    )
    await _seed_connection(
        session, workspace_id=ws_b.id, name="src", config={"database": str(db_b)}
    )
    await _seed_connection(
        session, workspace_id=ws_b.id, name="dst", config={"database": str(db_b)}
    )

    producer_cfg = {
        "name": "producer",
        "source": {"connection": "src", "query": "SELECT id, amount FROM raw"},
        "sink": {"connection": "dst", "table": "staging", "mode": "append"},
    }
    consumer_cfg = {
        "name": "consumer",
        "auto_materialize": True,
        "source": {"connection": "dst", "query": "SELECT id, amount FROM staging"},
        "sink": {"connection": "dst", "table": "mart", "mode": "append"},
    }

    p_a_prod, pv_a_prod = await _seed_pipeline(
        session, workspace_id=ws_a.id, name="prod", config=producer_cfg
    )
    p_a_cons, _ = await _seed_pipeline(
        session, workspace_id=ws_a.id, name="cons", config=consumer_cfg
    )
    p_b_prod, _ = await _seed_pipeline(
        session, workspace_id=ws_b.id, name="prod", config=producer_cfg
    )
    p_b_cons, _ = await _seed_pipeline(
        session, workspace_id=ws_b.id, name="cons", config=consumer_cfg
    )

    # Only A's producer is queued.
    await _seed_pending_run(
        session,
        workspace_id=ws_a.id,
        pipeline_id=p_a_prod.id,
        pipeline_version_id=pv_a_prod.id,
    )
    executed = await _drain_pending_runs(session, "hh1")
    # Exactly A's producer + A's consumer ran. B stayed quiet.
    assert executed == 2

    # A: both pipelines ran exactly once.
    runs_a_prod = await _runs_for_pipeline(session, p_a_prod.id)
    runs_a_cons = await _runs_for_pipeline(session, p_a_cons.id)
    assert len(runs_a_prod) == 1
    assert runs_a_prod[0].status == RunStatus.SUCCEEDED
    assert len(runs_a_cons) == 1
    assert runs_a_cons[0].status == RunStatus.SUCCEEDED
    # The auto-trigger stamped A's consumer (run lineage).
    assert runs_a_cons[0].result_json is not None
    assert runs_a_cons[0].result_json.get("triggered_by_run") == str(runs_a_prod[0].id)

    # B: zero runs on both sides — the trigger never crossed the boundary.
    assert await _runs_for_pipeline(session, p_b_prod.id) == []
    assert await _runs_for_pipeline(session, p_b_cons.id) == []

    # Data-plane confirmation: B's mart never got rows; B's staging never got
    # rows; A's chain wrote both downstream tables.
    def _rows(p: Path, sql: str) -> list[tuple]:
        conn = sqlite3.connect(str(p))
        try:
            return list(conn.execute(sql).fetchall())
        finally:
            conn.close()

    assert _rows(db_a, "SELECT id, amount FROM staging ORDER BY id") == [
        (1, 10),
        (2, 20),
    ]
    assert _rows(db_a, "SELECT id, amount FROM mart ORDER BY id") == [
        (1, 10),
        (2, 20),
    ]
    assert _rows(db_b, "SELECT id FROM staging") == []
    assert _rows(db_b, "SELECT id FROM mart") == []


# ===== HH2: Catalog rows are workspace-scoped ===============================


async def test_hh2_catalog_rows_are_workspace_scoped(session: AsyncSession, tmp_path: Path) -> None:
    """Both workspaces register the same asset_key strings (``src/raw``,
    ``dst/staging``); ``list_for_workspace`` must return only its
    workspace's rows. Cross-pipeline lineage stays inside the tenant."""
    db_a, db_b = _seed_two_ws_warehouse(tmp_path)

    ws_a = await _seed_workspace(session, slug="hh2-a")
    ws_b = await _seed_workspace(session, slug="hh2-b")
    for ws, db in ((ws_a, db_a), (ws_b, db_b)):
        await _seed_connection(
            session, workspace_id=ws.id, name="src", config={"database": str(db)}
        )
        await _seed_connection(
            session, workspace_id=ws.id, name="dst", config={"database": str(db)}
        )

    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, amount FROM raw"},
        "sink": {"connection": "dst", "table": "staging", "mode": "append"},
    }
    p_a, pv_a = await _seed_pipeline(session, workspace_id=ws_a.id, name="p", config=cfg)
    p_b, pv_b = await _seed_pipeline(session, workspace_id=ws_b.id, name="p", config=cfg)

    # Trigger both workspaces' pipelines so both register catalog rows.
    await _seed_pending_run(
        session,
        workspace_id=ws_a.id,
        pipeline_id=p_a.id,
        pipeline_version_id=pv_a.id,
    )
    await _seed_pending_run(
        session,
        workspace_id=ws_b.id,
        pipeline_id=p_b.id,
        pipeline_version_id=pv_b.id,
    )
    executed = await _drain_pending_runs(session, "hh2")
    assert executed == 2

    repo = AssetRepository(session)
    a_assets = await repo.list_for_workspace(workspace_id=ws_a.id)
    b_assets = await repo.list_for_workspace(workspace_id=ws_b.id)

    a_keys = {a.asset_key for a in a_assets}
    b_keys = {a.asset_key for a in b_assets}
    # Same asset key strings on both sides — that's exactly the
    # multi-tenant scenario we want.
    assert a_keys == b_keys == {"src/raw", "dst/staging"}
    # But the row IDs are *different* — independent catalog rows.
    a_id_by_key = {a.asset_key: a.id for a in a_assets}
    b_id_by_key = {a.asset_key: a.id for a in b_assets}
    for key in ("src/raw", "dst/staging"):
        assert a_id_by_key[key] != b_id_by_key[key], (
            f"asset row for {key} was shared across workspaces"
        )

    # Upstream lookup respects the row identity: A's staging is upstream
    # of A's raw only, never B's.
    a_staging = next(a for a in a_assets if a.asset_key == "dst/staging")
    b_staging = next(a for a in b_assets if a.asset_key == "dst/staging")
    a_upstream = await repo.upstream(a_staging.id)
    b_upstream = await repo.upstream(b_staging.id)
    a_upstream_ids = {a.id for a in a_upstream}
    b_upstream_ids = {a.id for a in b_upstream}
    assert a_upstream_ids == {a_id_by_key["src/raw"]}
    assert b_upstream_ids == {b_id_by_key["src/raw"]}
    # And critically: A's upstream isn't B's row, and vice versa.
    assert a_upstream_ids.isdisjoint(b_upstream_ids)
