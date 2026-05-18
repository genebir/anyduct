"""In-flight heartbeat updates from the executor (Step 9.3b).

While :class:`RunExecutor` has a pipeline running in
:func:`asyncio.to_thread`, a background asyncio task updates
``runs.heartbeat_at`` every ``_HEARTBEAT_INTERVAL_SECONDS``. The
reaper relies on this stamp to spot stuck workers.

Tests can't easily make a sqlite pipeline run "long enough" to
observe a real heartbeat (the test would have to wait at least one
interval), so we exercise the heartbeat helper directly with a short
interval. The executor path is exercised end-to-end in the worker
lifecycle tests; here we focus on the helper's behavior in
isolation.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from etlx_server.db.enums import RunStatus
from etlx_server.db.models import Pipeline, PipelineVersion, Run, Workspace
from etlx_server.worker.heartbeat import heartbeat_loop
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
) -> Run:
    r = Run(
        workspace_id=workspace_id,
        pipeline_id=pipeline_id,
        pipeline_version_id=pipeline_version_id,
        status=RunStatus.RUNNING,
        worker_id="worker-hb-test",
        started_at=datetime.now(UTC),
        heartbeat_at=datetime.now(UTC) - timedelta(hours=1),  # stale to start
    )
    session.add(r)
    await session.flush()
    return r


class _SessionFactoryAdapter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> _SessionFactoryAdapter:
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_: object) -> None:
        return None


# --- heartbeat_loop ---------------------------------------------------------


async def test_heartbeat_loop_advances_timestamp(session: AsyncSession) -> None:
    """Within one interval, ``heartbeat_at`` moves from the seeded stale value
    to a recent timestamp."""
    ws = await _seed_workspace(session, slug="hb-1")
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    run = await _seed_running_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await session.commit()
    initial = run.heartbeat_at
    assert initial is not None

    stop = asyncio.Event()
    factory = _SessionFactoryAdapter(session)
    task = asyncio.create_task(
        heartbeat_loop(factory, run.id, stop_event=stop, interval_seconds=0.1)
    )
    # Let at least 3 intervals pass.
    await asyncio.sleep(0.5)
    stop.set()
    await task

    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    assert refreshed.heartbeat_at is not None
    # The new heartbeat must be strictly more recent than the seeded
    # one (which was an hour in the past).
    assert refreshed.heartbeat_at > initial
    # And recent — within the last few seconds.
    age = datetime.now(UTC) - refreshed.heartbeat_at
    assert age < timedelta(seconds=5), age


async def test_heartbeat_loop_exits_immediately_when_stopped_first(
    session: AsyncSession,
) -> None:
    """A loop that finds ``stop_event`` already set must return without
    stamping anything (so a no-op execute path doesn't write spurious
    heartbeats)."""
    ws = await _seed_workspace(session, slug="hb-stop")
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    run = await _seed_running_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await session.commit()
    initial = run.heartbeat_at

    stop = asyncio.Event()
    stop.set()
    factory = _SessionFactoryAdapter(session)
    await heartbeat_loop(factory, run.id, stop_event=stop, interval_seconds=0.1)

    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    assert refreshed.heartbeat_at == initial  # untouched


async def test_heartbeat_loop_survives_db_error(session: AsyncSession) -> None:
    """A transient DB error during a heartbeat must not crash the loop —
    the in-flight pipeline doesn't care if one heartbeat misses."""

    class _FlakyFactory:
        """Yields a closed session the first call, the real one after."""

        def __init__(self, real: AsyncSession) -> None:
            self._real = real
            self._calls = 0

        def __call__(self) -> _FlakyFactory:
            self._calls += 1
            return self

        async def __aenter__(self) -> AsyncSession:
            # First call raises by yielding nothing usable.
            if self._calls == 1:
                raise RuntimeError("simulated DB hiccup")
            return self._real

        async def __aexit__(self, *_: object) -> None:
            return None

    ws = await _seed_workspace(session, slug="hb-flaky")
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    run = await _seed_running_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await session.commit()
    initial = run.heartbeat_at

    stop = asyncio.Event()
    factory = _FlakyFactory(session)
    task = asyncio.create_task(
        heartbeat_loop(factory, run.id, stop_event=stop, interval_seconds=0.1)
    )
    # Two intervals — first heartbeat fails, second succeeds.
    await asyncio.sleep(0.3)
    stop.set()
    await task

    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    # Despite the first iteration crashing, the loop kept going and a
    # later iteration updated the timestamp.
    assert refreshed.heartbeat_at is not None
    assert refreshed.heartbeat_at > initial
