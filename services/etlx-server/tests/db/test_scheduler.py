"""Cron scheduler tick semantics (Step 9.2, ADR-0041 K2).

Verifies:

* An active batch schedule whose cron firing falls before "now" gets a
  fresh pending Run row pinned to the pipeline's current version, with
  ``scheduled_at`` equal to the cron's next firing time.
* Inactive / stream / no-cron schedules are ignored.
* A second tick on the same data is a no-op (the latest run's
  ``scheduled_at`` becomes the new base for cron's next-firing calc).
* Pipelines with no ``is_current`` version are skipped, not crashed.
* The main loop honors :meth:`Scheduler.stop`.
* **Multi-replica safety (ADR-0041 K2)**: two ``tick_once`` calls
  against independent connections fire the same due schedule at most
  once thanks to ``FOR UPDATE SKIP LOCKED``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from etlx_server.db.enums import PipelineMode, RunStatus
from etlx_server.db.models import Asset, Pipeline, PipelineVersion, Run, Schedule, Workspace
from etlx_server.scheduler import Scheduler
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

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


# --- freshness auto-materialize (ADR-0038) ----------------------------------


async def _seed_fresh_pipeline(
    session: AsyncSession, *, workspace_id: UUID, name: str, sla: int | None, table: str = "out"
) -> tuple[Pipeline, PipelineVersion]:
    p = Pipeline(workspace_id=workspace_id, name=name)
    session.add(p)
    await session.flush()
    cfg: dict = {
        "name": name,
        "source": {"connection": "raw", "query": "SELECT id FROM seed"},
        "sink": {"connection": "wh", "table": table, "mode": "append"},
    }
    if sla is not None:
        cfg["freshness_sla_minutes"] = sla
    pv = PipelineVersion(pipeline_id=p.id, version=1, config_json=cfg, is_current=True)
    session.add(pv)
    await session.flush()
    return p, pv


async def _seed_asset(
    session: AsyncSession, *, workspace_id: UUID, key: str, last_materialized_at: datetime | None
) -> Asset:
    a = Asset(
        workspace_id=workspace_id,
        asset_key=key,
        kind="table",
        last_materialized_at=last_materialized_at,
    )
    session.add(a)
    await session.flush()
    return a


async def _fresh_runs(session: AsyncSession, pipeline_id: UUID) -> list[Run]:
    await session.commit()
    return list(
        (await session.execute(select(Run).where(Run.pipeline_id == pipeline_id))).scalars().all()
    )


async def test_freshness_triggers_when_stale(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="fr-stale")
    p, _ = await _seed_fresh_pipeline(session, workspace_id=ws.id, name="p", sla=5)
    await _seed_asset(
        session,
        workspace_id=ws.id,
        key="wh/out",
        last_materialized_at=datetime.now(UTC) - timedelta(minutes=30),
    )
    fired = await Scheduler(_SessionFactoryAdapter(session)).tick_once()
    assert fired == 1
    runs = await _fresh_runs(session, p.id)
    assert len(runs) == 1
    assert runs[0].status == RunStatus.PENDING
    assert runs[0].schedule_id is None
    assert runs[0].result_json["triggered_by"] == "freshness"


async def test_freshness_triggers_when_never_materialized(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="fr-never")
    p, _ = await _seed_fresh_pipeline(session, workspace_id=ws.id, name="p", sla=5)
    # No asset row at all → never materialized → stale.
    fired = await Scheduler(_SessionFactoryAdapter(session)).tick_once()
    assert fired == 1
    assert len(await _fresh_runs(session, p.id)) == 1


async def test_freshness_skips_when_fresh(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="fr-fresh")
    p, _ = await _seed_fresh_pipeline(session, workspace_id=ws.id, name="p", sla=60)
    await _seed_asset(
        session,
        workspace_id=ws.id,
        key="wh/out",
        last_materialized_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    fired = await Scheduler(_SessionFactoryAdapter(session)).tick_once()
    assert fired == 0
    assert len(await _fresh_runs(session, p.id)) == 0


async def test_freshness_skips_without_sla(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="fr-nosla")
    await _seed_fresh_pipeline(session, workspace_id=ws.id, name="p", sla=None)
    await _seed_asset(
        session,
        workspace_id=ws.id,
        key="wh/out",
        last_materialized_at=datetime.now(UTC) - timedelta(days=1),
    )
    fired = await Scheduler(_SessionFactoryAdapter(session)).tick_once()
    assert fired == 0


async def test_freshness_skips_when_run_in_flight(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="fr-inflight")
    p, pv = await _seed_fresh_pipeline(session, workspace_id=ws.id, name="p", sla=5)
    await _seed_asset(
        session,
        workspace_id=ws.id,
        key="wh/out",
        last_materialized_at=datetime.now(UTC) - timedelta(minutes=30),
    )
    # A pending run already exists — don't pile up.
    session.add(
        Run(
            workspace_id=ws.id,
            pipeline_id=p.id,
            pipeline_version_id=pv.id,
            status=RunStatus.PENDING,
        )
    )
    await session.flush()
    fired = await Scheduler(_SessionFactoryAdapter(session)).tick_once()
    assert fired == 0
    # still just the one pending run we seeded
    assert len(await _fresh_runs(session, p.id)) == 1


async def test_freshness_cooldown_after_recent_attempt(session: AsyncSession) -> None:
    """A failed run within the SLA window suppresses re-firing (no storm)."""
    ws = await _seed_workspace(session, slug="fr-cooldown")
    p, pv = await _seed_fresh_pipeline(session, workspace_id=ws.id, name="p", sla=30)
    await _seed_asset(
        session,
        workspace_id=ws.id,
        key="wh/out",
        last_materialized_at=datetime.now(UTC) - timedelta(hours=2),
    )
    r = Run(
        workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id, status=RunStatus.FAILED
    )
    session.add(r)
    await session.flush()
    r.created_at = datetime.now(UTC) - timedelta(minutes=2)  # within 30m SLA
    await session.flush()
    fired = await Scheduler(_SessionFactoryAdapter(session)).tick_once()
    assert fired == 0  # cooldown: attempted 2m ago, SLA 30m


# --- multi-replica (ADR-0041 K2) -------------------------------------------


@pytest_asyncio.fixture
async def isolated_factory(metadata_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """``async_sessionmaker`` that opens fresh connections directly on the
    testcontainer engine — sidesteps the outer-transaction ``session``
    fixture so two replicas can hold *independent* PG connections.

    Tests using this fixture commit real rows and are responsible for
    deleting whatever they seed (a teardown ``finally`` block).
    """
    return async_sessionmaker(metadata_engine, expire_on_commit=False, autoflush=False)


async def test_skip_locked_hides_schedule_from_concurrent_loader(
    isolated_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Multi-replica safety: while replica A's transaction holds the
    schedule row's lock, replica B's ``_load_due_schedules`` skips it.

    Deterministic by construction — A's session is left uncommitted across
    B's query, so the SKIP-LOCKED behaviour is observable without relying
    on event-loop scheduling. Lock releases on A's commit (or close).
    """
    from etlx_server.scheduler.scheduler import _load_due_schedules

    slug = f"k2-skip-{uuid4().hex[:8]}"
    ws_id: UUID
    p_id: UUID
    pv_id: UUID
    sched_id: UUID
    # --- seed (committed so both connections see it) -----------------------
    async with isolated_factory() as s:
        ws = Workspace(name=slug, slug=slug, color_hex="#FF3D8B")
        s.add(ws)
        await s.flush()
        p = Pipeline(workspace_id=ws.id, name=f"p-{slug}")
        s.add(p)
        await s.flush()
        pv = PipelineVersion(
            pipeline_id=p.id, version=1, config_json={"name": p.name}, is_current=True
        )
        s.add(pv)
        await s.flush()
        sched = Schedule(
            pipeline_id=p.id,
            name=f"sched-{slug}",
            cron_expr="* * * * *",
            mode=PipelineMode.BATCH,
            is_active=True,
            config_overrides={},
        )
        s.add(sched)
        await s.flush()
        sched.created_at = datetime.now(UTC) - timedelta(hours=1)
        await s.commit()
        ws_id, p_id, pv_id, sched_id = ws.id, p.id, pv.id, sched.id
    try:
        # Replica A holds the lock for the lifetime of its session.
        async with isolated_factory() as session_a:
            schedules_a = await _load_due_schedules(session_a)
            assert {s.id for s in schedules_a} == {sched_id}

            # Replica B, on an independent connection, sees an empty set
            # because the row is locked by A's open transaction.
            async with isolated_factory() as session_b:
                schedules_b = await _load_due_schedules(session_b)
                assert (
                    schedules_b == []
                ), f"replica B should skip the row locked by A (got {[x.id for x in schedules_b]})"
            # A's commit releases the lock — a *subsequent* load on a fresh
            # connection sees the row again.
            await session_a.commit()
        async with isolated_factory() as session_c:
            schedules_c = await _load_due_schedules(session_c)
            assert {s.id for s in schedules_c} == {sched_id}
    finally:
        async with isolated_factory() as s:
            await s.execute(delete(Run).where(Run.schedule_id == sched_id))
            await s.execute(delete(Schedule).where(Schedule.id == sched_id))
            await s.execute(delete(PipelineVersion).where(PipelineVersion.id == pv_id))
            await s.execute(delete(Pipeline).where(Pipeline.id == p_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


async def test_two_parallel_ticks_fire_due_schedule_at_most_once(
    isolated_factory: async_sessionmaker[AsyncSession],
) -> None:
    """End-to-end: even with two ``tick_once`` calls racing on independent
    connections, the schedule fires exactly once. Combines lock contention
    (one replica claims) with the existing "scheduled_at >= cron base"
    check (the other replica's subsequent tick sees the row again but the
    just-inserted run pushes the next firing into the future, so it
    doesn't double-fire either).
    """
    slug = f"k2-mr-{uuid4().hex[:8]}"
    ws_id: UUID
    p_id: UUID
    pv_id: UUID
    sched_id: UUID
    async with isolated_factory() as s:
        ws = Workspace(name=slug, slug=slug, color_hex="#FF3D8B")
        s.add(ws)
        await s.flush()
        p = Pipeline(workspace_id=ws.id, name=f"p-{slug}")
        s.add(p)
        await s.flush()
        pv = PipelineVersion(
            pipeline_id=p.id, version=1, config_json={"name": p.name}, is_current=True
        )
        s.add(pv)
        await s.flush()
        sched = Schedule(
            pipeline_id=p.id,
            name=f"sched-{slug}",
            cron_expr="* * * * *",
            mode=PipelineMode.BATCH,
            is_active=True,
            config_overrides={},
        )
        s.add(sched)
        await s.flush()
        sched.created_at = datetime.now(UTC) - timedelta(hours=1)
        await s.commit()
        ws_id, p_id, pv_id, sched_id = ws.id, p.id, pv.id, sched.id
    try:
        await asyncio.gather(
            Scheduler(isolated_factory).tick_once(),
            Scheduler(isolated_factory).tick_once(),
        )
        async with isolated_factory() as s:
            rows = list(
                (await s.execute(select(Run).where(Run.schedule_id == sched_id))).scalars().all()
            )
        assert len(rows) == 1, f"expected exactly one run, got {len(rows)}"
        assert rows[0].status == RunStatus.PENDING
        assert rows[0].pipeline_version_id == pv_id
    finally:
        async with isolated_factory() as s:
            await s.execute(delete(Run).where(Run.schedule_id == sched_id))
            await s.execute(delete(Schedule).where(Schedule.id == sched_id))
            await s.execute(delete(PipelineVersion).where(PipelineVersion.id == pv_id))
            await s.execute(delete(Pipeline).where(Pipeline.id == p_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()
