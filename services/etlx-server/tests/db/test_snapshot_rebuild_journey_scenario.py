"""Engineer persona: nightly snapshot replication with schema drift (Phase AAB).

The persona is an engineer who runs an end-of-day customer snapshot
from upstream Postgres (replicated as SQLite here for ergonomics)
into a local SQLite cache the analytics team queries the next
morning. The source schema mutates from day to day — yesterday's
``customers`` table had ``(id, name)``; today the upstream team
added ``email`` and removed ``name``. With ``auto_create_if_exists:
'drop'`` the engineer doesn't have to think about stale columns or
rows at all: every run rebuilds the sink to match the current source.

The flow is intentionally a *two-pass* run on the same workspace:

* Day 1 — source has ``(id, name)``. Sink starts empty so
  ``auto_create_table`` simply creates it.
* Day 2 — source now has ``(id, email)`` (we recreate the source DB).
  Same pipeline runs; ``drop`` wipes the day-1 table and rebuilds
  with ``(id, email)``. Yesterday's rows + the now-removed ``name``
  column are gone.

The test asserts the post-day-2 sink schema *only* has ``(id,
email)`` and the day-1 rows are gone. That is the user-visible
promise of the new ``drop`` mode.
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


def _seed_source_v1(src_path: Path) -> None:
    """Day-1 upstream: ``(id, name)``."""
    raw = sqlite3.connect(str(src_path))
    try:
        raw.execute("DROP TABLE IF EXISTS customers")
        raw.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
        raw.executemany(
            "INSERT INTO customers VALUES (?, ?)",
            [(1, "Alice"), (2, "Bob")],
        )
        raw.commit()
    finally:
        raw.close()


def _seed_source_v2(src_path: Path) -> None:
    """Day-2 upstream: ``(id, email)`` — ``name`` dropped, ``email``
    added. (We simulate the upstream DDL drift by recreating the
    source DB file.)"""
    raw = sqlite3.connect(str(src_path))
    try:
        raw.execute("DROP TABLE IF EXISTS customers")
        raw.execute("CREATE TABLE customers (id INTEGER, email TEXT)")
        raw.executemany(
            "INSERT INTO customers VALUES (?, ?)",
            [(10, "x@example.com"), (20, "y@example.com")],
        )
        raw.commit()
    finally:
        raw.close()


async def test_aab1_drop_rebuilds_sink_each_run(session: AsyncSession, tmp_path: Path) -> None:
    """Two-pass run with schema drift between days; ``drop`` keeps the
    sink schema honest. Asserts both *day-1 leftover columns gone*
    and *day-1 rows gone* — the two failure modes that ``skip`` would
    silently allow."""
    src_path = tmp_path / "upstream.db"
    dst_path = tmp_path / "snapshot.db"

    ws = await _seed_workspace(session, slug="aab1-snapshot")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(src_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(dst_path)}
    )
    cfg = {
        "name": "nightly_snapshot",
        "source": {
            "connection": "src",
            "query": "SELECT * FROM customers",
        },
        "sink": {
            "connection": "dst",
            "table": "customers_snapshot",
            "mode": "append",
            "auto_create_table": True,
            "auto_create_if_exists": "drop",
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="snapshot", config=cfg)

    # --- Day 1 -----------------------------------------------------------
    _seed_source_v1(src_path)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    day1 = await _drain_one(session, "aab1-day1")
    assert day1.status == RunStatus.SUCCEEDED

    out1 = sqlite3.connect(str(dst_path))
    try:
        cols1 = [
            row[1] for row in out1.execute('PRAGMA table_info("customers_snapshot")').fetchall()
        ]
        rows1 = sorted(out1.execute("SELECT id, name FROM customers_snapshot").fetchall())
    finally:
        out1.close()
    assert cols1 == ["id", "name"]
    assert rows1 == [(1, "Alice"), (2, "Bob")]

    # --- Day 2 — upstream schema drifted ---------------------------------
    _seed_source_v2(src_path)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    day2 = await _drain_one(session, "aab1-day2")
    assert day2.status == RunStatus.SUCCEEDED

    out2 = sqlite3.connect(str(dst_path))
    try:
        cols2 = [
            row[1] for row in out2.execute('PRAGMA table_info("customers_snapshot")').fetchall()
        ]
        rows2 = sorted(out2.execute("SELECT id, email FROM customers_snapshot").fetchall())
    finally:
        out2.close()
    # Day-1's ``name`` column is gone, day-2's ``email`` is in.
    assert cols2 == ["id", "email"]
    # Day-1's Alice/Bob rows are gone — drop wiped them. Only today's
    # snapshot.
    assert rows2 == [(10, "x@example.com"), (20, "y@example.com")]
