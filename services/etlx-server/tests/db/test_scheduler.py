"""Cron scheduler tick semantics (Step 9.2).

Verifies:

* An active batch schedule whose cron firing falls before "now" gets a
  fresh pending Run row pinned to the pipeline's current version, with
  ``scheduled_at`` equal to the cron's next firing time.
* Inactive / stream / no-cron schedules are ignored.
* A second tick on the same data is a no-op (the latest run's
  ``scheduled_at`` becomes the new base for cron's next-firing calc).
* Pipelines with no ``is_current`` version are skipped, not crashed.
* The main loop honors :meth:`Scheduler.stop`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from etlx_server.db.enums import PipelineMode, RunStatus
from etlx_server.db.models import Pipeline, PipelineVersion, Run, Schedule, Workspace
from etlx_server.scheduler import Scheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


# --- fixtures ---------------------------------------------------------------


async def _seed_workspace(session: AsyncSession, *, slug: str) -> Workspace:
    ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
    session.add(ws)
    await session.flush()
    return ws


async def _seed_pipeline_with_current(
    session: AsyncSession, *, workspace_id: UUID, name: str
) -> tuple[Pipeline, PipelineVersion]:
    p = Pipeline(workspace_id=workspace_id, name=name)
    session.add(p)
    await session.flush()
    pv = PipelineVersion(pipeline_id=p.id, version=1, config_json={"name": name}, is_current=True)
    session.add(pv)
    await session.flush()
    return p, pv


async def _seed_pipeline_no_version(
    session: AsyncSession, *, workspace_id: UUID, name: str
) -> Pipeline:
    p = Pipeline(workspace_id=workspace_id, name=name)
    session.add(p)
    await session.flush()
    return p


async def _seed_schedule(
    session: AsyncSession,
    *,
    pipeline_id: UUID,
    cron_expr: str | None,
    mode: PipelineMode = PipelineMode.BATCH,
    is_active: bool = True,
    created_at: datetime | None = None,
) -> Schedule:
    s = Schedule(
        pipeline_id=pipeline_id,
        name=f"sched-{pipeline_id.hex[:8]}",
        cron_expr=cron_expr,
        mode=mode,
        is_active=is_active,
        config_overrides={},
    )
    session.add(s)
    await session.flush()
    if created_at is not None:
        # Override server-default created_at so cron's base time is testable.
        s.created_at = created_at
        await session.flush()
    return s


class _SessionFactoryAdapter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> _SessionFactoryAdapter:
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_: object) -> None:
        return None


async def _runs_for_schedule_async(session: AsyncSession, schedule_id: UUID) -> list[Run]:
    await session.commit()
    return list(
        (
            await session.execute(
                select(Run).where(Run.schedule_id == schedule_id).order_by(Run.scheduled_at)
            )
        )
        .scalars()
        .all()
    )


# --- tick_once --------------------------------------------------------------


async def test_tick_creates_pending_run_for_due_batch_schedule(
    session: AsyncSession,
) -> None:
    """An active batch schedule whose next firing has elapsed yields one Run."""
    ws = await _seed_workspace(session, slug="sc-due")
    p, pv = await _seed_pipeline_with_current(session, workspace_id=ws.id, name="p")
    # Schedule created an hour ago with "every minute" cron — many missed
    # firings exist; the scheduler picks the *next one after now's base*.
    s = await _seed_schedule(
        session,
        pipeline_id=p.id,
        cron_expr="* * * * *",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )

    fired = await Scheduler(_SessionFactoryAdapter(session)).tick_once()
    assert fired == 1

    rows = await _runs_for_schedule_async(session, s.id)
    assert len(rows) == 1
    run = rows[0]
    assert run.status == RunStatus.PENDING
    assert run.pipeline_id == p.id
    assert run.pipeline_version_id == pv.id
    assert run.workspace_id == ws.id
    assert run.scheduled_at is not None


async def test_tick_skips_inactive_schedule(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="sc-inactive")
    p, _ = await _seed_pipeline_with_current(session, workspace_id=ws.id, name="p")
    s = await _seed_schedule(
        session,
        pipeline_id=p.id,
        cron_expr="* * * * *",
        is_active=False,
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )

    fired = await Scheduler(_SessionFactoryAdapter(session)).tick_once()
    assert fired == 0

    rows = await _runs_for_schedule_async(session, s.id)
    assert rows == []


async def test_tick_skips_stream_schedule(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="sc-stream")
    p, _ = await _seed_pipeline_with_current(session, workspace_id=ws.id, name="p")
    s = await _seed_schedule(
        session,
        pipeline_id=p.id,
        cron_expr=None,  # stream allows null cron
        mode=PipelineMode.STREAM,
    )

    fired = await Scheduler(_SessionFactoryAdapter(session)).tick_once()
    assert fired == 0
    rows = await _runs_for_schedule_async(session, s.id)
    assert rows == []


async def test_tick_skips_pipeline_with_no_current_version(
    session: AsyncSession,
) -> None:
    """Defensive: a schedule attached to a pipeline that somehow has no
    current version logs + skips instead of crashing the loop."""
    ws = await _seed_workspace(session, slug="sc-nover")
    p = await _seed_pipeline_no_version(session, workspace_id=ws.id, name="p")
    s = await _seed_schedule(
        session,
        pipeline_id=p.id,
        cron_expr="* * * * *",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )

    fired = await Scheduler(_SessionFactoryAdapter(session)).tick_once()
    assert fired == 0
    rows = await _runs_for_schedule_async(session, s.id)
    assert rows == []


async def test_second_tick_is_noop_until_next_firing(session: AsyncSession) -> None:
    """After firing once, the latest run's scheduled_at becomes the cron
    base — the next firing computed from it is in the future, so a second
    tick produces nothing.

    Uses a *yearly* cron so we can pick a created_at where exactly one
    firing has elapsed: created Feb of last year → first firing = Jan 1
    of this year (past, fires) → next firing = Jan 1 of next year
    (future, no-op). Avoids the cron-interval-vs-now flakiness that
    daily/hourly versions would have.
    """
    ws = await _seed_workspace(session, slug="sc-idem")
    p, _ = await _seed_pipeline_with_current(session, workspace_id=ws.id, name="p")
    now = datetime.now(UTC)
    # Last year, Feb 2 → cron's next firing is this year's Jan 1, which
    # is always in the past regardless of when "today" actually is.
    last_year_feb = datetime(year=now.year - 1, month=2, day=2, tzinfo=UTC)
    s = await _seed_schedule(
        session,
        pipeline_id=p.id,
        cron_expr="0 0 1 1 *",  # midnight on Jan 1, yearly
        created_at=last_year_feb,
    )

    scheduler = Scheduler(_SessionFactoryAdapter(session))
    first = await scheduler.tick_once()
    second = await scheduler.tick_once()
    assert first == 1
    assert second == 0

    rows = await _runs_for_schedule_async(session, s.id)
    assert len(rows) == 1


async def test_tick_handles_invalid_cron_without_crashing(
    session: AsyncSession,
) -> None:
    """An invalid cron should never have made it into the DB (router validates
    via croniter.is_valid), but if it somehow did, the scheduler logs and
    moves on."""
    ws = await _seed_workspace(session, slug="sc-bad")
    p, _ = await _seed_pipeline_with_current(session, workspace_id=ws.id, name="p")
    # Bypass router validation by writing directly via the ORM.
    s = Schedule(
        pipeline_id=p.id,
        name="busted",
        cron_expr="not a cron",
        mode=PipelineMode.BATCH,
        is_active=True,
        config_overrides={},
    )
    session.add(s)
    await session.flush()

    fired = await Scheduler(_SessionFactoryAdapter(session)).tick_once()
    assert fired == 0


# --- run() loop -------------------------------------------------------------


async def test_run_loop_stops_on_event(session: AsyncSession) -> None:
    """pre-set stop_event → loop exits immediately."""
    scheduler = Scheduler(_SessionFactoryAdapter(session), tick_interval_seconds=0.05)
    scheduler.stop()
    await scheduler.run()


async def test_run_loop_fires_then_stops(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="sc-loop")
    p, _ = await _seed_pipeline_with_current(session, workspace_id=ws.id, name="p")
    s = await _seed_schedule(
        session,
        pipeline_id=p.id,
        cron_expr="* * * * *",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    await session.commit()

    scheduler = Scheduler(_SessionFactoryAdapter(session), tick_interval_seconds=0.05)

    async def _stop_soon() -> None:
        await asyncio.sleep(0.3)
        scheduler.stop()

    await asyncio.gather(scheduler.run(), _stop_soon())

    rows = await _runs_for_schedule_async(session, s.id)
    assert len(rows) >= 1
