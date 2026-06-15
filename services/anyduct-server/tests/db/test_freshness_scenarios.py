"""Asset freshness (``freshness_sla_minutes``) dogfooding scenarios (Phase NN, 2026-05-29).

ADR-0038's time-axis auto-materialize: when a pipeline declares
``freshness_sla_minutes: N`` and one of its output assets hasn't been
materialised within the last N minutes, the scheduler's freshness tick
auto-enqueues a Run. The pipeline-axis cooldown (don't re-fire within
the SLA window) prevents a failing pipeline from storming the queue.

Two scenarios, each with sample data + time mocking via direct
``Asset.last_materialized_at`` writes (the only field the freshness
walker reads from the catalog):

* **NN1** — Stale upstream triggers freshness: run a pipeline once,
  then rewind ``last_materialized_at`` to 2x the SLA window. A direct
  call to ``Scheduler._tick_freshness(session, now)`` should enqueue
  a PENDING run. Drain it; the asset becomes fresh again.
* **NN2** — Within SLA → no fire: same setup but
  ``last_materialized_at`` is within the SLA window. ``_tick_freshness``
  returns 0; the queue stays empty.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from anyduct_server.assets.repository import AssetRepository
from anyduct_server.db.enums import RunStatus
from anyduct_server.db.models import Asset, Run
from anyduct_server.scheduler.scheduler import Scheduler
from anyduct_server.worker.claim import claim_pending_run
from anyduct_server.worker.executor import RunExecutor
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


def _seed_warehouse(tmp_path: Path) -> Path:
    db_path = tmp_path / "fr.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, value INTEGER)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, 10), (2, 20), (3, 30)])
        conn.execute("CREATE TABLE mart (id INTEGER, value INTEGER)")
        conn.commit()
    finally:
        conn.close()
    return db_path


async def _set_last_materialized_at(session: AsyncSession, *, asset_id, ts: datetime) -> None:
    """Force an asset row's ``last_materialized_at`` for time-axis
    simulation. The scheduler reads exactly this column to decide
    staleness."""
    await session.execute(update(Asset).where(Asset.id == asset_id).values(last_materialized_at=ts))
    await session.flush()


async def _pending_runs_for_pipeline(session: AsyncSession, pipeline_id) -> list[Run]:
    await session.commit()
    rows = await session.execute(
        select(Run)
        .where(Run.pipeline_id == pipeline_id, Run.status == RunStatus.PENDING)
        .order_by(Run.created_at)
    )
    return list(rows.scalars().all())


# ===== NN1: Stale → freshness enqueues a Run ================================


async def test_nn1_stale_asset_triggers_freshness_run(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Pipeline declares ``freshness_sla_minutes: 60``. After one normal
    run, we rewind ``last_materialized_at`` to 2 hours ago. A direct
    call to the scheduler's freshness tick should enqueue a new PENDING
    run (with ``result_json.triggered_by == 'freshness'``)."""
    db_path = _seed_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="nn1-fr")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    cfg = {
        "name": "p",
        "freshness_sla_minutes": 60,  # ADR-0038
        "source": {"connection": "src", "query": "SELECT id, value FROM raw"},
        "sink": {"connection": "dst", "table": "mart", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    # First manual run to materialise the catalog row.
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await _drain_pending_runs(session, "nn1-init")

    repo = AssetRepository(session)
    mart = next(
        a for a in await repo.list_for_workspace(workspace_id=ws.id) if a.asset_key == "dst/mart"
    )
    # Rewind last_materialized_at by 2 hours — well past the 60-min SLA.
    now = datetime.now(UTC)
    await _set_last_materialized_at(session, asset_id=mart.id, ts=now - timedelta(hours=2))
    # Also rewind the original run's created_at so the cooldown guard
    # (recent.created_at >= threshold) doesn't suppress the fire.
    await session.execute(
        update(Run).where(Run.pipeline_id == p.id).values(created_at=now - timedelta(hours=2))
    )
    await session.flush()

    # Tick freshness with the current ``now`` — should detect stale +
    # enqueue exactly one PENDING run for our pipeline.
    scheduler = Scheduler(_SessionFactoryAdapter(session))  # type: ignore[arg-type]
    fired = await scheduler._tick_freshness(session, now)
    await session.commit()
    assert fired == 1

    pending = await _pending_runs_for_pipeline(session, p.id)
    assert len(pending) == 1
    assert pending[0].result_json == {"triggered_by": "freshness", "sla_minutes": 60}

    # Drain the freshness-triggered run; the asset becomes fresh again.
    executed = await _drain_pending_runs(session, "nn1-fr")
    assert executed == 1
    refreshed = await session.get(Asset, mart.id)
    assert refreshed is not None
    assert refreshed.last_materialized_at is not None
    assert refreshed.last_materialized_at >= now


# ===== NN2: Within SLA → no fire ============================================


async def test_nn2_within_sla_does_not_fire_freshness(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Same shape as NN1 but ``last_materialized_at`` is only 10 minutes
    ago (SLA is 60). The freshness tick must not enqueue anything —
    otherwise a healthy pipeline would storm the queue."""
    db_path = _seed_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="nn2-fr")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    cfg = {
        "name": "p",
        "freshness_sla_minutes": 60,
        "source": {"connection": "src", "query": "SELECT id, value FROM raw"},
        "sink": {"connection": "dst", "table": "mart", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await _drain_pending_runs(session, "nn2-init")

    # last_materialized_at is "now-ish" by default; leave it as-is. The
    # freshness walker should see it as fresh.
    scheduler = Scheduler(_SessionFactoryAdapter(session))  # type: ignore[arg-type]
    fired = await scheduler._tick_freshness(session, datetime.now(UTC))
    await session.commit()
    assert fired == 0

    pending = await _pending_runs_for_pipeline(session, p.id)
    assert pending == []
