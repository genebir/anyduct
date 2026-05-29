"""Cursor-based backfill dogfooding scenarios (Phase JJ, 2026-05-29).

ADR-0039 added incremental backfill: a run can carry a cursor range in
``result_json.backfill`` and the worker passes ``cursor_from`` /
``cursor_to`` through to ``Pipeline.run``. The pipeline then routes
through ``source.read_since`` (instead of the default ``read``) and
honours the exclusive lower / inclusive upper bound.

Two scenarios with sample data:

* **JJ1** — backfill respects the bounds: 10 rows seeded by date; a
  backfill over a 3-day window pulls exactly 3 rows into the sink.
* **JJ2** — full run + backfill together: a normal manual run reads
  all 10 rows, a follow-up backfill of a different window appends 3
  more, and the catalog records both materializations as separate
  audit-trail entries on the *same* asset row.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from etlx_server.assets.repository import AssetRepository
from etlx_server.db.enums import RunStatus
from etlx_server.db.models import Run
from etlx_server.worker.claim import claim_pending_run
from etlx_server.worker.executor import RunExecutor
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import StaticSecretBackend
from tests.db.test_worker_lifecycle import (
    _seed_connection,
    _seed_pipeline,
    _seed_workspace,
    _SessionFactoryAdapter,
)

pytestmark = pytest.mark.asyncio


# ----- helpers ---------------------------------------------------------------


async def _seed_backfill_run(
    session: AsyncSession,
    *,
    workspace_id: Any,
    pipeline: Any,
    pipeline_version: Any,
    cursor_from: str | None,
    cursor_to: str | None,
) -> Run:
    """Insert a PENDING run whose ``result_json.backfill`` carries the
    cursor range — mirrors what ``POST /pipelines/{id}/backfill`` does."""
    r = Run(
        workspace_id=workspace_id,
        pipeline_id=pipeline.id,
        pipeline_version_id=pipeline_version.id,
        status=RunStatus.PENDING,
        result_json={
            "source": "backfill",
            "backfill": {"cursor_from": cursor_from, "cursor_to": cursor_to},
        },
    )
    session.add(r)
    await session.flush()
    return r


async def _run_pending(session: AsyncSession, worker_id: str) -> Run:
    claimed = await claim_pending_run(session, worker_id=worker_id)
    assert claimed is not None
    await session.commit()
    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    ).execute(claimed.id)
    rows = await session.execute(select(Run).where(Run.id == claimed.id))
    return rows.scalar_one()


def _query_rows(db_path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(str(db_path))
    try:
        return list(conn.execute(sql).fetchall())
    finally:
        conn.close()


def _seed_ten_day_warehouse(tmp_path: Path) -> Path:
    """Ten rows, one per day from 2026-05-01 to 2026-05-10. Sink is
    empty so the backfill scenarios can count exactly what landed."""
    db_path = tmp_path / "bf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw_events (id INTEGER, created_at TEXT, value INTEGER)")
        conn.executemany(
            "INSERT INTO raw_events VALUES (?, ?, ?)",
            [(i, f"2026-05-{i:02d}", i * 10) for i in range(1, 11)],
        )
        conn.execute("CREATE TABLE replicated_events (id INTEGER, created_at TEXT, value INTEGER)")
        conn.commit()
    finally:
        conn.close()
    return db_path


# ===== JJ1: Backfill respects cursor_from / cursor_to =======================


async def test_jj1_backfill_pulls_only_the_window(session: AsyncSession, tmp_path: Path) -> None:
    """A backfill over ``cursor_from='2026-05-02' / cursor_to='2026-05-05'``
    should pull exactly the rows with ``created_at`` in
    ``(2026-05-02, 2026-05-05]`` — exclusive lower, inclusive upper
    (ADR-0039 §"range semantics"). That's 3 rows: days 3, 4, 5.

    The other 7 rows (days 1, 2, 6-10) must stay out of the sink — we're
    verifying the windowing actually works rather than reading the whole
    source and counting matches after.
    """
    db_path = _seed_ten_day_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="jj1-bf")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    cfg = {
        "name": "p",
        "source": {
            "connection": "src",
            "query": "SELECT id, created_at, value FROM raw_events",
            # The cursor column is what ``source.read_since`` filters on.
            "cursor_column": "created_at",
        },
        "sink": {"connection": "dst", "table": "replicated_events", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    backfill_run = await _seed_backfill_run(
        session,
        workspace_id=ws.id,
        pipeline=p,
        pipeline_version=pv,
        cursor_from="2026-05-02",
        cursor_to="2026-05-05",
    )

    finished = await _run_pending(session, "jj1")
    assert finished.status == RunStatus.SUCCEEDED
    # The backfill marker survives on the row so an operator can later
    # audit "this run was a backfill of (cursor_from, cursor_to]".
    assert finished.result_json is not None
    assert finished.result_json.get("source") == "backfill"
    assert finished.result_json["backfill"] == {
        "cursor_from": "2026-05-02",
        "cursor_to": "2026-05-05",
    }

    rows = _query_rows(
        db_path,
        "SELECT id, created_at, value FROM replicated_events ORDER BY id",
    )
    # Days 3, 4, 5 — exclusive lower bound (day 2 not included), inclusive
    # upper bound (day 5 included).
    assert rows == [
        (3, "2026-05-03", 30),
        (4, "2026-05-04", 40),
        (5, "2026-05-05", 50),
    ]
    assert finished.records_read == 3
    assert finished.records_written == 3
    # Backfill run id matches.
    assert finished.id == backfill_run.id


# ===== JJ2: Full run + backfill, catalog records both materializations =====


async def test_jj2_full_run_then_backfill_materializations_count(
    session: AsyncSession, tmp_path: Path
) -> None:
    """First run: no cursor bounds → reads all 10 rows. Second run:
    backfill over a small window → reads 3 rows. The catalog should
    show:

    * the same asset row across both runs (idempotent upsert),
    * exactly two AssetMaterialization entries (audit trail).
    """
    db_path = _seed_ten_day_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="jj2-bf")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    cfg = {
        "name": "p",
        "source": {
            "connection": "src",
            "query": "SELECT id, created_at, value FROM raw_events",
            "cursor_column": "created_at",
        },
        "sink": {"connection": "dst", "table": "replicated_events", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)

    # 1) Full run — no cursor bounds → all 10 rows.
    from tests.db.test_worker_lifecycle import _seed_pending_run

    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    full = await _run_pending(session, "jj2-full")
    assert full.records_read == 10
    assert full.records_written == 10

    # 2) Backfill over days 7..10 (so we re-pull a tail window). Append
    # mode means it shows up alongside the previous rows.
    await _seed_backfill_run(
        session,
        workspace_id=ws.id,
        pipeline=p,
        pipeline_version=pv,
        cursor_from="2026-05-06",
        cursor_to="2026-05-10",
    )
    bf = await _run_pending(session, "jj2-bf")
    assert bf.records_read == 4  # days 7, 8, 9, 10 (exclusive of day 6 lower)
    assert bf.records_written == 4

    # Data plane: 10 + 4 = 14 rows landed (idempotency is the upstream
    # warehouse's job; here we're just confirming the cursor logic).
    assert len(_query_rows(db_path, "SELECT id FROM replicated_events")) == 14

    # Catalog: same asset row across both runs, two materialization entries.
    repo = AssetRepository(session)
    assets = {a.asset_key: a for a in await repo.list_for_workspace(workspace_id=ws.id)}
    sink = assets["dst/replicated_events"]
    mats = await repo.materializations(asset_id=sink.id)
    assert len(mats) == 2, "expected one materialization per run (full + backfill)"
    # ``records_written`` on each materialization mirrors the run's count.
    written = sorted(m.records_written for m in mats)
    assert written == [4, 10]
