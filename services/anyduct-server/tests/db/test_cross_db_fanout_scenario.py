"""Cross-DB fan-out with selective auto_create_table (Phase ZZ, 2026-05-29).

User persona: an engineer fans the same source out to two destinations —
one sink they want auto-created (a new analytics sandbox), one sink
that already exists (the team-shared warehouse table). The pipeline
``sinks`` list carries different ``auto_create_table`` flags per
entry; the runtime should honour each flag independently.

Scenario:

* Source sqlite with ``orders(id, amount)``.
* Sink #1 (``analytics`` sandbox): a *fresh* sqlite file with no
  ``orders_copy`` table. ``auto_create_table: true``.
* Sink #2 (``warehouse``): a sqlite file where ``orders_copy``
  already exists with the right shape. ``auto_create_table`` *not* set.

After the run both sinks have the rows; the auto-create only ran on
sink #1; the existing table on sink #2 stayed untouched (no
``IF NOT EXISTS`` dance changed its declared shape).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
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


async def test_zz1_fanout_only_auto_creates_flagged_sinks(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Two sinks, only one with ``auto_create_table: true``. The
    runtime must auto-create the flagged sink (table doesn't exist)
    and leave the unflagged sink alone (table already exists with the
    right shape)."""
    src_path = tmp_path / "src.db"
    analytics_path = tmp_path / "analytics.db"
    warehouse_path = tmp_path / "warehouse.db"

    raw = sqlite3.connect(str(src_path))
    try:
        raw.execute("CREATE TABLE orders (id INTEGER, amount INTEGER)")
        raw.executemany(
            "INSERT INTO orders VALUES (?, ?)",
            [(1, 100), (2, 250), (3, 75)],
        )
        raw.commit()
    finally:
        raw.close()

    # analytics file has no ``orders_copy`` yet — auto_create handles it.
    sqlite3.connect(str(analytics_path)).close()
    # warehouse file already has the table with the right shape.
    wh = sqlite3.connect(str(warehouse_path))
    try:
        wh.execute("CREATE TABLE orders_copy (id INTEGER, amount INTEGER, batch TEXT)")
        wh.commit()
    finally:
        wh.close()

    ws = await _seed_workspace(session, slug="zz1-fanout")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(src_path)}
    )
    await _seed_connection(
        session,
        workspace_id=ws.id,
        name="analytics",
        config={"database": str(analytics_path)},
    )
    await _seed_connection(
        session,
        workspace_id=ws.id,
        name="warehouse",
        config={"database": str(warehouse_path)},
    )

    cfg = {
        "name": "fanout_orders",
        "source": {
            "connection": "src",
            "query": "SELECT id, amount FROM orders",
        },
        "sinks": [
            {
                "connection": "analytics",
                "table": "orders_copy",
                "mode": "append",
                "auto_create_table": True,
            },
            {
                "connection": "warehouse",
                "table": "orders_copy",
                "mode": "append",
                # No auto_create_table — table already exists.
            },
        ],
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    finished = await _run_one(session, "zz1")
    assert finished.status == RunStatus.SUCCEEDED

    # analytics sink: table auto-created from source schema.
    out = sqlite3.connect(str(analytics_path))
    try:
        info = out.execute('PRAGMA table_info("orders_copy")').fetchall()
        col_types = {row[1]: row[2] for row in info}
        rows_a = sorted(out.execute("SELECT id, amount FROM orders_copy").fetchall())
    finally:
        out.close()
    assert col_types == {"id": "INTEGER", "amount": "INTEGER"}, col_types
    assert rows_a == [(1, 100), (2, 250), (3, 75)]

    # warehouse sink: pre-existing table untouched (still has the
    # ``batch`` column it had before — auto_create_table=False respects
    # the user-declared schema even if the source is narrower).
    out = sqlite3.connect(str(warehouse_path))
    try:
        info = out.execute('PRAGMA table_info("orders_copy")').fetchall()
        col_names = [row[1] for row in info]
        rows_w = sorted(out.execute("SELECT id, amount FROM orders_copy").fetchall())
    finally:
        out.close()
    assert col_names == ["id", "amount", "batch"], col_names
    assert rows_w == [(1, 100), (2, 250), (3, 75)]
