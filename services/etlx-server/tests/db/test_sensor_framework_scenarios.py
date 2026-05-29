"""Sensor framework (K3) dogfooding scenarios (Phase RR, 2026-05-29).

ADR-0041 K3 introduced a sensor framework: workspace-scoped event
triggers that fire a target pipeline when a check returns
``triggered=True``. The built-in ``asset_freshness`` sensor watches a
catalog ``asset_key`` and fires when the asset's
``last_materialized_at`` is older than ``max_age_minutes`` (the
asset-axis dual of pipeline-axis ``freshness_sla_minutes``).

The unit side of these builtins is well-tested in isolation. This
module exercises the integration: a real catalog row (worker
materialises it) + a real sensor row + ``SensorScheduler.tick_once``
must enqueue (or skip) a Run depending on the actual catalog state.

Two scenarios:

* **RR1** — Stale asset triggers the sensor. After one normal run we
  rewind ``last_materialized_at``; the sensor scheduler's tick should
  enqueue a PENDING run on the target pipeline.
* **RR2** — Fresh asset within the budget does not trigger. Same setup
  without rewind; the tick records a "fresh" check_result but enqueues
  nothing.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from etlx_server.assets.repository import AssetRepository
from etlx_server.db.enums import RunStatus
from etlx_server.db.models import Asset, Run, Sensor
from etlx_server.sensors.scheduler import SensorScheduler
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
    db_path = tmp_path / "rr.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, value INTEGER)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, 10), (2, 20)])
        conn.execute("CREATE TABLE mart (id INTEGER, value INTEGER)")
        conn.commit()
    finally:
        conn.close()
    return db_path


async def _seed_sensor(
    session: AsyncSession,
    *,
    workspace_id,
    name: str,
    asset_key: str,
    max_age_minutes: int,
    target_pipeline_id,
    poll_interval_seconds: int = 60,
) -> Sensor:
    """Insert an ``asset_freshness`` sensor row pointing at a target
    pipeline. The scheduler's tick will dispatch by ``type``."""
    sensor = Sensor(
        workspace_id=workspace_id,
        name=name,
        type="asset_freshness",
        config_json={"asset_key": asset_key, "max_age_minutes": max_age_minutes},
        target_pipeline_id=target_pipeline_id,
        poll_interval_seconds=poll_interval_seconds,
        is_active=True,
    )
    session.add(sensor)
    await session.flush()
    return sensor


async def _pending_runs_for_pipeline(session: AsyncSession, pipeline_id) -> list[Run]:
    await session.commit()
    rows = await session.execute(
        select(Run)
        .where(Run.pipeline_id == pipeline_id, Run.status == RunStatus.PENDING)
        .order_by(Run.created_at)
    )
    return list(rows.scalars().all())


# ===== RR1: Stale asset triggers the sensor =================================


async def test_rr1_asset_freshness_sensor_fires_on_stale_catalog(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Pipeline materialises ``dst/mart``. We rewind its
    ``last_materialized_at`` to 3 hours ago. The asset-freshness sensor
    with a 60-min budget should fire on the next tick and enqueue a
    PENDING run of the target pipeline."""
    db_path = _seed_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="rr1-sensor")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, value FROM raw"},
        "sink": {"connection": "dst", "table": "mart", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    # First manual run to materialise the catalog row.
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await _drain_pending_runs(session, "rr1-init")

    repo = AssetRepository(session)
    mart = next(
        a for a in await repo.list_for_workspace(workspace_id=ws.id) if a.asset_key == "dst/mart"
    )
    # Rewind well past the 60-min budget so the sensor sees ``stale``.
    now = datetime.now(UTC)
    await session.execute(
        update(Asset)
        .where(Asset.id == mart.id)
        .values(last_materialized_at=now - timedelta(hours=3))
    )
    await session.flush()

    sensor = await _seed_sensor(
        session,
        workspace_id=ws.id,
        name="mart_freshness",
        asset_key="dst/mart",
        max_age_minutes=60,
        target_pipeline_id=p.id,
    )
    await session.commit()

    scheduler = SensorScheduler(_SessionFactoryAdapter(session))  # type: ignore[arg-type]
    fired = await scheduler.tick_once()
    assert fired == 1

    # The sensor row records the check (last_check_at + last_triggered_at).
    refreshed = await session.get(Sensor, sensor.id)
    assert refreshed is not None
    assert refreshed.last_check_at is not None
    assert refreshed.last_triggered_at is not None
    assert refreshed.last_result_json is not None
    assert refreshed.last_result_json.get("triggered") is True
    assert "stale" in (refreshed.last_result_json.get("message") or "")

    # A PENDING run on the target pipeline now exists, stamped as sensor-driven.
    pending = await _pending_runs_for_pipeline(session, p.id)
    assert len(pending) == 1


# ===== RR2: Fresh asset within budget — no trigger ==========================


async def test_rr2_asset_freshness_sensor_skips_when_fresh(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Same shape as RR1 but the catalog row is freshly materialised
    (default ``last_materialized_at`` is "now-ish"). The sensor must
    record a non-triggered result and enqueue nothing."""
    db_path = _seed_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="rr2-sensor")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, value FROM raw"},
        "sink": {"connection": "dst", "table": "mart", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await _drain_pending_runs(session, "rr2-init")

    sensor = await _seed_sensor(
        session,
        workspace_id=ws.id,
        name="mart_freshness",
        asset_key="dst/mart",
        max_age_minutes=60,
        target_pipeline_id=p.id,
    )
    await session.commit()

    scheduler = SensorScheduler(_SessionFactoryAdapter(session))  # type: ignore[arg-type]
    fired = await scheduler.tick_once()
    assert fired == 0

    refreshed = await session.get(Sensor, sensor.id)
    assert refreshed is not None
    assert refreshed.last_check_at is not None
    assert refreshed.last_triggered_at is None
    assert refreshed.last_result_json is not None
    assert refreshed.last_result_json.get("triggered") is False
    assert "fresh" in (refreshed.last_result_json.get("message") or "")

    pending = await _pending_runs_for_pipeline(session, p.id)
    assert pending == []
