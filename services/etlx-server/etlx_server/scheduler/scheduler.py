"""Scheduler — turn active cron schedules into pending Run rows.

One pass (``tick_once``) walks every active ``batch`` :class:`Schedule`,
figures out whether its next firing time has elapsed, and if so enqueues
a fresh pending Run pinned to the pipeline's current version. The
:class:`Scheduler.run` loop calls ``tick_once`` on a configurable
interval until :meth:`stop` is invoked.

Catchup policy: **no catchup**. If the scheduler was down for a long
time and 30 firings were missed, only the *next* firing (post-now) gets
enqueued. Backfilling 30 runs at once is a thundering-herd footgun;
operators who actually want backfills should compose explicit Run rows
via the API.

Concurrency (ADR-0041 K2): multi-replica safe. ``_load_due_schedules``
locks each schedule row with ``FOR UPDATE SKIP LOCKED`` for the
duration of the tick's transaction — two replicas calling
``tick_once`` concurrently see disjoint sets of schedules, so no
double-fire on the same row. Freshness ticking (``_tick_freshness``)
isn't locked yet; its per-pipeline cooldown bounds the harm of an
occasional double-fire (a follow-up slice can add proper distributed
locking there).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from croniter import croniter  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from etl_plugins.config.models import PipelineConfig
from etl_plugins.runtime.lineage import derive_lineage
from etlx_server.db.enums import PipelineMode, RunStatus
from etlx_server.db.models import Asset, Pipeline, PipelineVersion, Run, Schedule

# Runs in pending/running are "in flight" — don't pile up a fresh trigger on top.
_IN_FLIGHT = (RunStatus.PENDING, RunStatus.RUNNING)

logger = logging.getLogger(__name__)


class Scheduler:
    """Async cron tick loop."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        tick_interval_seconds: float = 10.0,
    ) -> None:
        self._factory = factory
        self._tick_interval = tick_interval_seconds
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        """Drive the tick loop until :meth:`stop` is called."""
        logger.info("scheduler starting (tick_interval=%.1fs)", self._tick_interval)
        while not self._stop_event.is_set():
            try:
                fired = await self.tick_once()
            except Exception:
                logger.exception("scheduler: tick failed")
                fired = 0
            if fired:
                logger.info("scheduler: enqueued %d new run(s)", fired)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._tick_interval)
        logger.info("scheduler stopped")

    async def tick_once(self) -> int:
        """One pass over active schedules; return number of Runs enqueued."""
        now = datetime.now(UTC)
        async with self._factory() as session:
            schedules = await _load_due_schedules(session)
            fired = 0
            for schedule in schedules:
                next_fire = await _compute_next_firing(session, schedule, now)
                if next_fire is None or next_fire > now:
                    continue
                # Fetch the current pipeline version atomically — a pipeline
                # without one isn't safe to run, but it shouldn't happen via
                # the public API (POST /pipelines always inserts v1).
                current = await _get_current_version(session, schedule.pipeline_id)
                if current is None:
                    logger.warning(
                        "schedule %s skipped — pipeline %s has no current version",
                        schedule.id,
                        schedule.pipeline_id,
                    )
                    continue
                pipeline = await _get_pipeline(session, schedule.pipeline_id)
                if pipeline is None:
                    continue
                session.add(
                    Run(
                        workspace_id=pipeline.workspace_id,
                        pipeline_id=pipeline.id,
                        pipeline_version_id=current.id,
                        schedule_id=schedule.id,
                        status=RunStatus.PENDING,
                        scheduled_at=next_fire,
                    )
                )
                fired += 1
            fired += await self._tick_freshness(session, now)
            if fired:
                await session.commit()
            return fired

    async def _tick_freshness(self, session: AsyncSession, now: datetime) -> int:
        """Enqueue runs for pipelines whose output assets are staler than their
        ``freshness_sla_minutes`` (ADR-0038). Guards: skip if a run is already
        in flight, and don't re-fire more than once per SLA window (so a failing
        pipeline doesn't storm). Caller commits."""
        rows = (
            await session.execute(
                select(
                    Pipeline.id,
                    Pipeline.workspace_id,
                    PipelineVersion.id,
                    PipelineVersion.config_json,
                )
                .join(PipelineVersion, PipelineVersion.pipeline_id == Pipeline.id)
                .where(PipelineVersion.is_current.is_(True))
            )
        ).all()

        fired = 0
        for pipeline_id, ws_id, version_id, config_json in rows:
            cfg_dict = config_json or {}
            sla = cfg_dict.get("freshness_sla_minutes")
            if not isinstance(sla, int) or sla <= 0:
                continue
            threshold = now - timedelta(minutes=sla)

            recent = (
                await session.execute(
                    select(Run.status, Run.created_at)
                    .where(Run.pipeline_id == pipeline_id)
                    .order_by(Run.created_at.desc())
                    .limit(1)
                )
            ).first()
            if recent is not None:
                if recent[0] in _IN_FLIGHT:
                    continue  # already pending/running
                if recent[1] >= threshold:
                    continue  # attempted within the SLA window — cooldown

            try:
                cfg = PipelineConfig.model_validate(cfg_dict)
            except Exception:
                continue
            if cfg.mode != PipelineMode.BATCH.value:
                continue
            out_keys = [str(k) for k in derive_lineage(cfg).outputs]
            if not out_keys:
                continue

            mat_rows = (
                await session.execute(
                    select(Asset.asset_key, Asset.last_materialized_at).where(
                        Asset.workspace_id == ws_id, Asset.asset_key.in_(out_keys)
                    )
                )
            ).all()
            mat: dict[str, datetime | None] = {}
            for key, lm in mat_rows:
                mat[key] = lm

            # Stale if any output was never materialized or is older than the SLA.
            stale = False
            for k in out_keys:
                lm = mat.get(k)
                if lm is None or lm < threshold:
                    stale = True
                    break
            if not stale:
                continue

            session.add(
                Run(
                    workspace_id=ws_id,
                    pipeline_id=pipeline_id,
                    pipeline_version_id=version_id,
                    schedule_id=None,
                    status=RunStatus.PENDING,
                    result_json={"triggered_by": "freshness", "sla_minutes": sla},
                )
            )
            fired += 1
            logger.info("scheduler: freshness trigger for pipeline %s (sla=%dm)", pipeline_id, sla)
        return fired

    def stop(self) -> None:
        """Request graceful shutdown — loop exits after current tick."""
        self._stop_event.set()


# --- helpers ----------------------------------------------------------------


async def _load_due_schedules(session: AsyncSession) -> list[Schedule]:
    """Active batch schedules with a cron expression. Stream schedules pass.

    Each row is loaded with ``FOR UPDATE SKIP LOCKED`` (ADR-0041 K2) so
    two scheduler replicas running ``tick_once`` concurrently see
    disjoint partitions of the active set — the second replica's rows
    are silently skipped during the first replica's tick and picked up
    on the next pass. Locks release when the caller's transaction
    commits (or its session closes).
    """
    stmt = (
        select(Schedule)
        .where(Schedule.is_active.is_(True))
        .where(Schedule.mode == PipelineMode.BATCH)
        .where(Schedule.cron_expr.is_not(None))
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _last_scheduled_at(session: AsyncSession, schedule_id: UUID) -> datetime | None:
    """Most recent ``scheduled_at`` among runs created from this schedule."""
    stmt = (
        select(Run.scheduled_at)
        .where(Run.schedule_id == schedule_id)
        .order_by(Run.scheduled_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _compute_next_firing(
    session: AsyncSession, schedule: Schedule, now: datetime
) -> datetime | None:
    """Return the next-firing-after-base time, or None if no cron / invalid."""
    if not schedule.cron_expr:
        return None
    last = await _last_scheduled_at(session, schedule.id)
    # First-ever firing: use the schedule's creation time as the base so
    # we don't backfill from epoch.
    base = last or schedule.created_at or (now - timedelta(seconds=1))
    try:
        cron = croniter(schedule.cron_expr, base)
        return cron.get_next(datetime)  # type: ignore[no-any-return]
    except (ValueError, KeyError):
        logger.warning(
            "schedule %s has invalid cron %r — skipping",
            schedule.id,
            schedule.cron_expr,
        )
        return None


async def _get_current_version(session: AsyncSession, pipeline_id: UUID) -> PipelineVersion | None:
    stmt = (
        select(PipelineVersion)
        .where(PipelineVersion.pipeline_id == pipeline_id)
        .where(PipelineVersion.is_current.is_(True))
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _get_pipeline(session: AsyncSession, pipeline_id: UUID) -> Pipeline | None:
    return (
        await session.execute(select(Pipeline).where(Pipeline.id == pipeline_id))
    ).scalar_one_or_none()


__all__ = ["Scheduler"]
