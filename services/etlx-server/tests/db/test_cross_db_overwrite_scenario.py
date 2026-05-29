"""Cross-DB overwrite + auto_create_table — analyst persona (Phase AAD).

The persona is an analyst who points the dashboard at a small
SQLite cache and refreshes it every hour with the *current state*
of upstream (we model upstream as another SQLite for ergonomics).
They want each refresh to be **idempotent** — running the pipeline
twice in a row with no upstream change must leave the sink in the
exact same shape (no duplicated rows, no schema drift).

This is the cross-DB equivalent of "snapshot replication" — the
sink is a snapshot of the source as of run time.

The persona test runs the pipeline twice:

* Pass 1 — empty sink. ``auto_create_table`` creates it,
  ``mode=overwrite`` writes the source rows.
* Pass 2 — sink already has Pass-1's rows. Without idempotency, you
  would either error on table-exists or end up with duplicates.
  ``mode=overwrite`` re-writes the same shape.

We assert the post-pass-2 sink has *exactly* the source rows once
(not twice), and the schema is unchanged.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from etlx_server.db.enums import RunStatus
from etlx_server.db.models import Run
from etlx_server.worker.claim import claim_pending_run
from etlx_server.worker.executor import RunExecutor
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


async def _drain_one(session: AsyncSession, worker_id: str) -> Run:
    claimed = await claim_pending_run(session, worker_id=worker_id)
    assert claimed is not None
    await session.commit()
    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    ).execute(claimed.id)
    rows = await session.execute(select(Run).where(Run.id == claimed.id))
    return rows.scalar_one()


def _seed_source(src_path: Path) -> None:
    raw = sqlite3.connect(str(src_path))
    try:
        raw.execute("DROP TABLE IF EXISTS metrics")
        raw.execute("CREATE TABLE metrics (region TEXT, value INTEGER)")
        raw.executemany(
            "INSERT INTO metrics VALUES (?, ?)",
            [("APAC", 100), ("EMEA", 200), ("AMER", 300)],
        )
        raw.commit()
    finally:
        raw.close()


async def test_aad1_overwrite_with_auto_create_is_idempotent(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Two passes against unchanged source. Post-pass-2 sink has the
    exact source rows once — no duplicates, no schema drift."""
    src_path = tmp_path / "upstream.db"
    dst_path = tmp_path / "dashboard.db"

    ws = await _seed_workspace(session, slug="aad1-overwrite")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(src_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(dst_path)}
    )
    cfg = {
        "name": "dashboard_refresh",
        "source": {
            "connection": "src",
            "query": "SELECT region, value FROM metrics",
        },
        "sink": {
            "connection": "dst",
            "table": "metrics_cache",
            "mode": "overwrite",
            "auto_create_table": True,
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="dashboard", config=cfg)

    _seed_source(src_path)

    # Pass 1 — empty sink → auto-created + populated.
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    pass1 = await _drain_one(session, "aad1-p1")
    assert (
        pass1.status == RunStatus.SUCCEEDED
    ), f"pass-1 failed: {pass1.error_class}: {pass1.error_message}"

    out1 = sqlite3.connect(str(dst_path))
    try:
        rows1 = sorted(out1.execute("SELECT region, value FROM metrics_cache").fetchall())
        cols1 = [row[1] for row in out1.execute('PRAGMA table_info("metrics_cache")').fetchall()]
    finally:
        out1.close()
    assert cols1 == ["region", "value"]
    assert rows1 == [("AMER", 300), ("APAC", 100), ("EMEA", 200)]

    # Pass 2 — sink exists; overwrite makes the rerun idempotent.
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    pass2 = await _drain_one(session, "aad1-p2")
    assert (
        pass2.status == RunStatus.SUCCEEDED
    ), f"pass-2 failed: {pass2.error_class}: {pass2.error_message}"

    out2 = sqlite3.connect(str(dst_path))
    try:
        rows2 = sorted(out2.execute("SELECT region, value FROM metrics_cache").fetchall())
        cols2 = [row[1] for row in out2.execute('PRAGMA table_info("metrics_cache")').fetchall()]
    finally:
        out2.close()
    # Same shape, same rows — no duplicates, no drift.
    assert cols2 == ["region", "value"]
    assert rows2 == [("AMER", 300), ("APAC", 100), ("EMEA", 200)]
