"""ZombieReaper scan + reap behavior (Step 9.3b).

Covers:

* Stale ``running`` rows (``heartbeat_at`` older than the threshold)
  transition to ``failed`` with ``error_class='ZombieReaped'``.
* Fresh ``running`` rows + terminal rows + ``running`` rows with
  ``heartbeat_at=NULL`` (no heartbeat yet — claim just happened) stay
  put.
* ``reap_once`` is idempotent — a second call on the same data
  reaps nothing more.
* The reaper's main loop honors :meth:`stop`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from anyduct_server.db.enums import RunStatus
from anyduct_server.db.models import Pipeline, PipelineVersion, Run, Workspace
from anyduct_server.worker.reaper import ZombieReaper
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _seed_workspace(session: AsyncSession, *, slug: str) -> Workspace:
    ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
    session.add(ws)
    await session.flush()
    return ws


async def _seed_pipeline(
    session: AsyncSession, *, workspace_id: UUID, name: str
) -> tuple[Pipeline, PipelineVersion]:
    p = Pipeline(workspace_id=workspace_id, name=name)
    session.add(p)
    await session.flush()
    pv = PipelineVersion(pipeline_id=p.id, version=1, config_json={"name": name}, is_current=True)
    session.add(pv)
    await session.flush()
    return p, pv


async def _seed_running_run(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    pipeline_id: UUID,
    pipeline_version_id: UUID,
    worker_id: str,
    heartbeat_at: datetime | None,
) -> Run:
    r = Run(
        workspace_id=workspace_id,
        pipeline_id=pipeline_id,
        pipeline_version_id=pipeline_version_id,
        status=RunStatus.RUNNING,
        worker_id=worker_id,
        started_at=datetime.now(UTC) - timedelta(minutes=5),
        heartbeat_at=heartbeat_at,
    )
    session.add(r)
    await session.flush()
    return r


class _SessionFactoryAdapter:
    """Reuse the test ``session`` for both the reaper's scan + the test's
    assertions, so commits become savepoint releases inside the conftest
    outer transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> _SessionFactoryAdapter:
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_: object) -> None:
        return None


# --- reap_once ---------------------------------------------------------------


async def test_reap_marks_stale_running_as_failed(session: AsyncSession) -> None:
    """A running row whose heartbeat is older than the threshold gets reaped."""
    ws = await _seed_workspace(session, slug="zr-stale")
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    zombie = await _seed_running_run(
        session,
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        worker_id="dead-worker",
        heartbeat_at=datetime.now(UTC) - timedelta(minutes=10),
    )

    reaper = ZombieReaper(
        _SessionFactoryAdapter(session),
        heartbeat_timeout_seconds=30.0,
    )
    reaped = await reaper.reap_once()
    assert reaped == 1

    await session.commit()
    refreshed = (await session.execute(select(Run).where(Run.id == zombie.id))).scalar_one()
    assert refreshed.status == RunStatus.FAILED
    assert refreshed.error_class == "ZombieReaped"
    assert refreshed.error_message is not None
    assert "dead-worker" in refreshed.error_message
    assert refreshed.finished_at is not None


async def test_reap_ignores_fresh_running(session: AsyncSession) -> None:
    """A row whose heartbeat is recent must NOT be reaped."""
    ws = await _seed_workspace(session, slug="zr-fresh")
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    fresh = await _seed_running_run(
        session,
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        worker_id="alive-worker",
        heartbeat_at=datetime.now(UTC) - timedelta(seconds=5),
    )

    reaper = ZombieReaper(
        _SessionFactoryAdapter(session),
        heartbeat_timeout_seconds=60.0,
    )
    reaped = await reaper.reap_once()
    assert reaped == 0

    await session.commit()
    refreshed = (await session.execute(select(Run).where(Run.id == fresh.id))).scalar_one()
    assert refreshed.status == RunStatus.RUNNING


async def test_reap_ignores_null_heartbeat(session: AsyncSession) -> None:
    """A claim that just happened (heartbeat_at NULL) shouldn't be reaped — the
    worker hasn't had time to stamp it yet. The claim itself stamps a
    heartbeat in practice, but we defend against the edge case anyway."""
    ws = await _seed_workspace(session, slug="zr-null")
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    running = await _seed_running_run(
        session,
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        worker_id="brand-new",
        heartbeat_at=None,
    )

    reaper = ZombieReaper(
        _SessionFactoryAdapter(session),
        heartbeat_timeout_seconds=30.0,
    )
    reaped = await reaper.reap_once()
    assert reaped == 0

    await session.commit()
    refreshed = (await session.execute(select(Run).where(Run.id == running.id))).scalar_one()
    assert refreshed.status == RunStatus.RUNNING


async def test_reap_ignores_terminal_rows(session: AsyncSession) -> None:
    """succeeded/failed/cancelled rows must not be touched."""
    ws = await _seed_workspace(session, slug="zr-terminal")
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    for label in ("succeeded", "failed", "cancelled"):
        r = Run(
            workspace_id=ws.id,
            pipeline_id=p.id,
            pipeline_version_id=pv.id,
            status=RunStatus(label),
            worker_id="some-worker",
            heartbeat_at=long_ago,
            finished_at=long_ago,
        )
        session.add(r)
    await session.flush()

    reaper = ZombieReaper(
        _SessionFactoryAdapter(session),
        heartbeat_timeout_seconds=30.0,
    )
    reaped = await reaper.reap_once()
    assert reaped == 0


async def test_reap_is_idempotent(session: AsyncSession) -> None:
    """Calling reap_once twice on the same dataset reaps once, not twice."""
    ws = await _seed_workspace(session, slug="zr-idem")
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    zombie = await _seed_running_run(
        session,
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        worker_id="dead",
        heartbeat_at=datetime.now(UTC) - timedelta(minutes=10),
    )

    reaper = ZombieReaper(
        _SessionFactoryAdapter(session),
        heartbeat_timeout_seconds=30.0,
    )
    assert await reaper.reap_once() == 1
    assert await reaper.reap_once() == 0  # second scan finds nothing

    await session.commit()
    refreshed = (await session.execute(select(Run).where(Run.id == zombie.id))).scalar_one()
    assert refreshed.status == RunStatus.FAILED


async def test_reap_respects_batch_limit(session: AsyncSession) -> None:
    """Three stale rows + batch_limit=2 → first scan reaps 2, next reaps 1."""
    ws = await _seed_workspace(session, slug="zr-batch")
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    for _ in range(3):
        await _seed_running_run(
            session,
            workspace_id=ws.id,
            pipeline_id=p.id,
            pipeline_version_id=pv.id,
            worker_id="dead",
            heartbeat_at=datetime.now(UTC) - timedelta(minutes=10),
        )

    reaper = ZombieReaper(
        _SessionFactoryAdapter(session),
        heartbeat_timeout_seconds=30.0,
        batch_limit=2,
    )
    assert await reaper.reap_once() == 2
    assert await reaper.reap_once() == 1
    assert await reaper.reap_once() == 0


# --- run() loop --------------------------------------------------------------


async def test_run_loop_stops_on_stop_event(session: AsyncSession) -> None:
    """Loop exits cleanly when stop() is called before run()."""
    reaper = ZombieReaper(
        _SessionFactoryAdapter(session),
        heartbeat_timeout_seconds=30.0,
        scan_interval_seconds=0.05,
    )
    reaper.stop()
    await reaper.run()


async def test_run_loop_reaps_then_stops(session: AsyncSession) -> None:
    """A stale row gets reaped during one of the scans, then stop() exits the loop."""
    ws = await _seed_workspace(session, slug="zr-loop")
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    zombie = await _seed_running_run(
        session,
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        worker_id="dead",
        heartbeat_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    await session.commit()

    reaper = ZombieReaper(
        _SessionFactoryAdapter(session),
        heartbeat_timeout_seconds=30.0,
        scan_interval_seconds=0.05,
    )

    async def _stop_after_short_delay() -> None:
        await asyncio.sleep(0.3)
        reaper.stop()

    await asyncio.gather(reaper.run(), _stop_after_short_delay())

    refreshed = (await session.execute(select(Run).where(Run.id == zombie.id))).scalar_one()
    assert refreshed.status == RunStatus.FAILED
    assert refreshed.error_class == "ZombieReaped"
