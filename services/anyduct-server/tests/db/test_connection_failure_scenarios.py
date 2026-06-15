"""Connection failure dogfooding scenarios (Phase OO, 2026-05-29).

Two of the most common operational failures don't come from user code
or the pipeline shape — they come from the *connection*:

* The database file the connection points to doesn't exist (typo, dev
  env not provisioned).
* The connection itself is fine, but the source query references a
  table that doesn't exist (drift, dropped table, wrong env).

Both should land the run in ``failed`` status and leave the catalog
untouched — same posture as Phase EE's Scenario F (transform raised)
and Phase II's DLQ-failure path, just at different stages of the
pipeline lifecycle.

Two scenarios:

* **OO1** — Invalid database path. The connector fails at ``connect()``
  time. Run status becomes ``failed``; no asset rows show up.
* **OO2** — Source table missing. ``connect()`` succeeds; the source
  read raises when it tries to query. Run status becomes ``failed``;
  no asset rows show up (the catalog never sees a successful
  materialisation, so the source table itself also stays out of the
  catalog — that's the right truthful state).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

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


async def _run_one(session: AsyncSession, worker_id: str) -> Run:
    claimed = await claim_pending_run(session, worker_id=worker_id)
    assert claimed is not None
    await session.commit()
    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    ).execute(claimed.id)
    rows = await session.execute(select(Run).where(Run.id == claimed.id))
    return rows.scalar_one()


# ===== OO1: Invalid database path → connect failure ========================


async def test_oo1_invalid_database_path_run_fails_catalog_clean(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The ``database`` config points at a directory we know doesn't
    exist + can't be created in the test sandbox. The sqlite connector
    raises during ``connect()``; the worker catches it, the run lands
    in ``failed`` status, and the catalog stays empty because the
    success-path lineage persistence never ran (same posture as Phase
    EE's Scenario F)."""
    ws = await _seed_workspace(session, slug="oo1-bad-path")
    bogus = "/nope/does/not/exist/at/all.db"
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": bogus})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": bogus})
    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT 1 AS x"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    finished = await _run_one(session, "oo1")

    assert finished.status == RunStatus.FAILED
    assert finished.error_class is not None
    assert finished.error_message is not None and finished.error_message != ""

    # Catalog stayed clean — the failed-run policy is "no half-written
    # rows" (same as Phase FF Scenario F).
    repo = AssetRepository(session)
    keys = {a.asset_key for a in await repo.list_for_workspace(workspace_id=ws.id)}
    assert keys == set(), f"expected empty catalog, got {sorted(keys)}"


# ===== OO2: Source table missing → read failure ============================


async def test_oo2_source_table_missing_run_fails_catalog_clean(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Connection is valid (the sqlite file exists), but the source
    query references a table that isn't there. ``read()`` raises;
    the worker catches it, the run fails, and the catalog stays
    clean."""
    db_path = tmp_path / "missing-table.db"
    conn = sqlite3.connect(str(db_path))
    try:
        # Only ``out`` exists — the source pipeline points at ``raw``
        # which we deliberately don't create.
        conn.execute("CREATE TABLE out (id INTEGER)")
        conn.commit()
    finally:
        conn.close()

    ws = await _seed_workspace(session, slug="oo2-missing-table")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id FROM raw"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    finished = await _run_one(session, "oo2")

    assert finished.status == RunStatus.FAILED
    assert finished.error_message is not None
    assert (
        "raw" in finished.error_message.lower() or "no such table" in finished.error_message.lower()
    )

    # Catalog clean: neither the (nonexistent) ``src/raw`` nor the
    # ``dst/out`` sink should land — Phase FF Scenario F's posture
    # generalises across all failure stages.
    repo = AssetRepository(session)
    keys = {a.asset_key for a in await repo.list_for_workspace(workspace_id=ws.id)}
    assert keys == set(), f"expected empty catalog, got {sorted(keys)}"
