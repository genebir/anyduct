"""Cron schedule dogfooding scenarios (Phase SS, 2026-05-29).

The cron scheduler (Step 9.2 / ADR-0021) is the third axis in the
time-based trigger family:

* **Pipeline-axis freshness** (Phase NN / ADR-0038) — produces "is this
  pipeline's output stale?" tick.
* **Asset-axis sensor** (Phase RR / ADR-0041 K3) — consumers declare
  "fire when this asset is stale".
* **Cron schedule** (this slice) — operator says "run this pipeline at
  these times of day".

Two scenarios with sample data + time mocking via the
``schedules.created_at`` baseline:

* **SS1** — Due schedule fires + drain produces sink rows. We seed a
  schedule with ``cron_expr='* * * * *'`` and rewind ``created_at``
  one minute into the past. The next ``Scheduler.tick_once`` evaluates
  the cron against ``last or created_at`` and decides it's due.
* **SS2** — Inactive schedule never fires. Same setup but
  ``is_active=False``; the loader skips it.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from anyduct_server.db.enums import PipelineMode, RunStatus
from anyduct_server.db.models import Run, Schedule
from anyduct_server.scheduler.scheduler import Scheduler
from anyduct_server.worker.claim import claim_pending_run
from anyduct_server.worker.executor import RunExecutor
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import StaticSecretBackend
from tests.db.test_worker_lifecycle import (
    _seed_connection,
    _seed_pipeline,
    _seed_workspace,
    _SessionFactoryAdapter,
)

pytestmark = pytest.mark.asyncio


async def _drain_pending_runs(session: AsyncSession, worker_id: str) -> int:
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


async def _seed_schedule(
    session: AsyncSession,
    *,
    pipeline_id,
    name: str,
    cron_expr: str | None,
    is_active: bool = True,
    rewind_created_minutes: int = 0,
) -> Schedule:
    """Insert a schedule + optionally rewind ``created_at`` so the
    scheduler's first-firing base is in the past."""
    s = Schedule(
        pipeline_id=pipeline_id,
        name=name,
        cron_expr=cron_expr,
        mode=PipelineMode.BATCH,
        is_active=is_active,
        config_overrides={},
    )
    session.add(s)
    await session.flush()
    if rewind_created_minutes:
        await session.execute(
            update(Schedule)
            .where(Schedule.id == s.id)
            .values(created_at=datetime.now(UTC) - timedelta(minutes=rewind_created_minutes))
        )
        await session.flush()
    return s


async def _runs_for_pipeline(session: AsyncSession, pipeline_id) -> list[Run]:
    await session.commit()
    rows = await session.execute(
        select(Run).where(Run.pipeline_id == pipeline_id).order_by(Run.created_at)
    )
    return list(rows.scalars().all())


def _seed_warehouse(tmp_path: Path) -> Path:
    db_path = tmp_path / "ss.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, value INTEGER)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, 100), (2, 200)])
        conn.execute("CREATE TABLE out (id INTEGER, value INTEGER)")
        conn.commit()
    finally:
        conn.close()
    return db_path


# ===== SS1: Due cron schedule fires + drain ================================


async def test_ss1_due_cron_schedule_fires_and_run_drains_to_sink(
    session: AsyncSession, tmp_path: Path
) -> None:
    """``cron_expr='* * * * *'`` runs every minute. We rewind
    ``schedules.created_at`` by 2 minutes so the first-firing base is
    well in the past; the next cron evaluation lands 1 minute ago →
    due. ``Scheduler.tick_once`` should enqueue a PENDING run; the
    worker drain then writes to the sink table."""
    db_path = _seed_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="ss1-cron")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, value FROM raw"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    p, _pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_schedule(
        session,
        pipeline_id=p.id,
        name="every-minute",
        cron_expr="* * * * *",
        rewind_created_minutes=2,
    )
    await session.commit()

    # Tick once: should enqueue the due run.
    scheduler = Scheduler(_SessionFactoryAdapter(session))  # type: ignore[arg-type]
    fired = await scheduler.tick_once()
    assert fired == 1

    # Drain — the worker claims + executes the freshly-queued run.
    executed = await _drain_pending_runs(session, "ss1")
    assert executed == 1

    runs = await _runs_for_pipeline(session, p.id)
    assert len(runs) == 1
    assert runs[0].status == RunStatus.SUCCEEDED
    # The Run row remembers which schedule produced it.
    assert runs[0].schedule_id is not None
    # And the sink received the source rows.
    conn = sqlite3.connect(str(db_path))
    try:
        rows = list(conn.execute("SELECT id, value FROM out ORDER BY id"))
    finally:
        conn.close()
    assert rows == [(1, 100), (2, 200)]


# ===== SS2: Inactive schedule does not fire ================================


async def test_ss2_inactive_schedule_is_skipped(session: AsyncSession, tmp_path: Path) -> None:
    """Same shape as SS1 but ``is_active=False``. The loader should skip
    the row; ``tick_once`` returns 0 and the queue stays empty."""
    db_path = _seed_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="ss2-cron-inactive")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, value FROM raw"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    p, _ = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_schedule(
        session,
        pipeline_id=p.id,
        name="paused",
        cron_expr="* * * * *",
        is_active=False,
        rewind_created_minutes=2,
    )
    await session.commit()

    scheduler = Scheduler(_SessionFactoryAdapter(session))  # type: ignore[arg-type]
    fired = await scheduler.tick_once()
    assert fired == 0

    runs = await _runs_for_pipeline(session, p.id)
    assert runs == []
