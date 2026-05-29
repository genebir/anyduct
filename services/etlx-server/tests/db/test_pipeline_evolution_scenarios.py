"""Pipeline evolution dogfooding scenarios (Phase PP, 2026-05-29).

Operators edit pipeline configs all the time — rename a sink table,
add a JOIN'd dimension, rewire which raw tables feed the staging
layer. The catalog needs to reflect those changes accurately *without*
losing the trail of what an asset *used to be*.

Two scenarios, each with sample data + a second PipelineVersion:

* **PP1** — Sink table renamed (v1: ``stage_v1`` → v2: ``stage_v2``).
  Both runs succeed. The catalog ends with *both* asset rows; each
  has exactly one materialization (the run that wrote it). Operators
  can still find the deprecated row, and the new row appears in
  every lineage query.
* **PP2** — Source query gains a JOIN'd dimension between v1 and v2.
  v2's run registers the extra base table as a new input asset +
  emits the extra ``input → sink`` edge — the catalog DAG widens to
  match the new config.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from etlx_server.assets.repository import AssetRepository
from etlx_server.db.models import PipelineVersion, Run
from etlx_server.worker.claim import claim_pending_run
from etlx_server.worker.executor import RunExecutor
from sqlalchemy import select, update
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


async def _run_one(session: AsyncSession, worker_id: str) -> Run:
    claimed = await claim_pending_run(session, worker_id=worker_id)
    assert claimed is not None
    await session.commit()
    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    ).execute(claimed.id)
    rows = await session.execute(select(Run).where(Run.id == claimed.id))
    return rows.scalar_one()


async def _add_pipeline_version(
    session: AsyncSession,
    *,
    pipeline_id,
    version: int,
    config_json: dict,
) -> PipelineVersion:
    """Insert a new PipelineVersion and flip ``is_current`` flags so the
    new row is the only one marked current — mirrors how
    ``PipelineRepository.ensure_version`` behaves at the API layer."""
    await session.execute(
        update(PipelineVersion)
        .where(PipelineVersion.pipeline_id == pipeline_id)
        .values(is_current=False)
    )
    pv = PipelineVersion(
        pipeline_id=pipeline_id,
        version=version,
        config_json=config_json,
        is_current=True,
    )
    session.add(pv)
    await session.flush()
    return pv


def _seed_chain_warehouse(tmp_path: Path) -> Path:
    db_path = tmp_path / "pp.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, value INTEGER)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, 10), (2, 20)])
        # Stage targets for PP1.
        conn.execute("CREATE TABLE stage_v1 (id INTEGER, value INTEGER)")
        conn.execute("CREATE TABLE stage_v2 (id INTEGER, value INTEGER)")
        # PP2 extras.
        conn.execute("CREATE TABLE dim_country (id INTEGER, country TEXT)")
        conn.executemany("INSERT INTO dim_country VALUES (?, ?)", [(1, "KR"), (2, "US")])
        conn.execute("CREATE TABLE joined (id INTEGER, value INTEGER, country TEXT)")
        conn.commit()
    finally:
        conn.close()
    return db_path


# ===== PP1: Sink rename — both rows + their own materializations ===========


async def test_pp1_sink_table_rename_keeps_old_row_and_adds_new(
    session: AsyncSession, tmp_path: Path
) -> None:
    """v1 writes to ``stage_v1`` and the catalog records that materialization.
    v2 writes to ``stage_v2``. After both runs the catalog has *both*
    rows; each has exactly one materialisation entry (no double counting
    onto either side)."""
    db_path = _seed_chain_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="pp1-rename")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    cfg_v1 = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, value FROM raw"},
        "sink": {"connection": "dst", "table": "stage_v1", "mode": "append"},
    }
    p, pv1 = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg_v1)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv1.id
    )
    await _run_one(session, "pp1-v1")

    repo = AssetRepository(session)
    assets_after_v1 = {a.asset_key: a for a in await repo.list_for_workspace(workspace_id=ws.id)}
    assert "dst/stage_v1" in assets_after_v1
    v1_sink = assets_after_v1["dst/stage_v1"]
    mats_v1 = await repo.materializations(asset_id=v1_sink.id)
    assert len(mats_v1) == 1

    # ---- ship v2: sink table renamed ----
    cfg_v2 = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, value FROM raw"},
        "sink": {"connection": "dst", "table": "stage_v2", "mode": "append"},
    }
    pv2 = await _add_pipeline_version(session, pipeline_id=p.id, version=2, config_json=cfg_v2)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv2.id
    )
    await _run_one(session, "pp1-v2")

    assets_after_v2 = {a.asset_key: a for a in await repo.list_for_workspace(workspace_id=ws.id)}
    # The old row is still there — operators can audit "this asset used
    # to be populated, now it isn't".
    assert "dst/stage_v1" in assets_after_v2
    assert "dst/stage_v2" in assets_after_v2

    v2_sink = assets_after_v2["dst/stage_v2"]
    # Each row gets exactly its own run as a materialisation.
    assert len(await repo.materializations(asset_id=v1_sink.id)) == 1
    assert len(await repo.materializations(asset_id=v2_sink.id)) == 1
    # The v1 sink's last_materialized_at hasn't moved — it's now stale.
    refreshed_v1 = await session.get(type(v1_sink), v1_sink.id)
    refreshed_v2 = await session.get(type(v2_sink), v2_sink.id)
    assert refreshed_v1 is not None and refreshed_v2 is not None
    assert refreshed_v1.last_materialized_at is not None
    assert refreshed_v2.last_materialized_at is not None
    assert refreshed_v2.last_materialized_at >= refreshed_v1.last_materialized_at


# ===== PP2: Source query gains a JOIN'd dimension ===========================


async def test_pp2_source_gains_join_dimension_adds_input_asset_and_edge(
    session: AsyncSession, tmp_path: Path
) -> None:
    """v1's source reads only ``raw``. v2's source JOINs ``dim_country``.
    After v2's run the catalog should have ``src/dim_country`` as an
    input asset and an edge ``src/dim_country → dst/joined``."""
    db_path = _seed_chain_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="pp2-join")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    # ---- v1: only raw ----
    cfg_v1 = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, value FROM raw"},
        "sink": {"connection": "dst", "table": "joined", "mode": "append"},
    }
    p, pv1 = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg_v1)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv1.id
    )
    await _run_one(session, "pp2-v1")

    repo = AssetRepository(session)
    assets_after_v1 = {a.asset_key: a for a in await repo.list_for_workspace(workspace_id=ws.id)}
    assert "src/raw" in assets_after_v1
    assert "src/dim_country" not in assets_after_v1  # not joined yet
    joined_asset = assets_after_v1["dst/joined"]
    v1_upstreams = {a.asset_key for a in await repo.upstream(joined_asset.id)}
    assert v1_upstreams == {"src/raw"}

    # ---- v2: source JOINs dim_country ----
    cfg_v2 = {
        "name": "p",
        "source": {
            "connection": "src",
            "query": (
                "SELECT r.id, r.value, c.country " "FROM raw r JOIN dim_country c ON c.id = r.id"
            ),
        },
        "sink": {"connection": "dst", "table": "joined", "mode": "append"},
    }
    pv2 = await _add_pipeline_version(session, pipeline_id=p.id, version=2, config_json=cfg_v2)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv2.id
    )
    await _run_one(session, "pp2-v2")

    assets_after_v2 = {a.asset_key: a for a in await repo.list_for_workspace(workspace_id=ws.id)}
    # New input asset arrived — Phase X follow-up's ``extract_referenced_tables``
    # added it during this run's lineage emit.
    assert "src/dim_country" in assets_after_v2

    # The sink's upstream set widened to include both raw + dim_country.
    joined_v2 = assets_after_v2["dst/joined"]
    v2_upstreams = {a.asset_key for a in await repo.upstream(joined_v2.id)}
    assert v2_upstreams == {"src/raw", "src/dim_country"}

    # The sink row identity is the same across versions (same connection/
    # table), so its materialization count grew from 1 to 2.
    mats = await repo.materializations(asset_id=joined_v2.id)
    assert len(mats) == 2
