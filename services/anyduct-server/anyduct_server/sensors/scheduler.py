"""SensorScheduler — poll active sensors, enqueue runs on trigger
(ADR-0041 K3b).

Mirrors the cron :class:`anyduct_server.scheduler.Scheduler` shape: a tick
loop that walks active rows, decides which are *due* (last_check_at +
poll_interval_seconds <= now), runs each :func:`build_sensor`'s
``check()``, and on ``triggered=True`` enqueues a PENDING Run pinned
to the sensor's ``target_pipeline_id`` (+ the pipeline's current
version).

Multi-replica safe (K2 pattern): the due-sensor query holds
``FOR UPDATE SKIP LOCKED`` for the lifetime of the tick's transaction,
so two scheduler replicas see disjoint partitions of the work and a
sensor can't fire twice per poll. Failover is the same shape as
K2/K2b: a dead replica releases its locks; the surviving replica picks
the row up on the next tick.

What this scheduler does NOT do (deliberately):
    * No retry / backoff policy on a sensor that keeps returning
      ``triggered=False``. ``poll_interval_seconds`` is the only knob.
    * No de-duplication. If a sensor fires every tick it enqueues a
      run every tick. Sensors are *idempotent* on the check side; the
      target pipeline's own idempotency handling (`pre_sql`, asset
      lineage replace semantics, …) is the right layer for "don't run
      twice".
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Register service-side sensor builtins (asset_freshness, future
# file_landed/lineage). Pure-core builtins (``http``) self-register on
# their own module import — the routers/scheduler already pull them in.
import anyduct_server.sensors.builtins  # noqa: F401 — side-effect import
from anyduct_server.db.enums import RunStatus
from anyduct_server.db.models import Pipeline, PipelineVersion, Run, Sensor
from anyduct_server.sensors.context import use_sensor_context
from anyduct_server.sensors.repository import SensorRepository
from etl_plugins.config.secrets import SecretBackend
from etl_plugins.core.exceptions import ConfigError
from etl_plugins.core.sensor import build_sensor

logger = logging.getLogger(__name__)

# Floor on poll_interval_seconds at tick time so a typo (e.g. ``1``)
# doesn't hammer downstream services. The repository accepts smaller
# values; the scheduler just won't poll faster than this.
_MIN_POLL_INTERVAL_SECONDS = 5.0


class SensorScheduler:
    """Async tick loop polling active :class:`Sensor` rows."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        tick_interval_seconds: float = 5.0,
        secret_backend: SecretBackend | None = None,
    ) -> None:
        self._factory = factory
        self._tick_interval = tick_interval_seconds
        # Optional — surfaced to sensors via ContextVar so service-side
        # builtins (file_landed) can resolve ``${SECRET:<path>}`` inside
        # a referenced Connection's config. Pure builtins (http) don't
        # care; pass ``None`` if the deployment doesn't use secrets.
        self._secret_backend = secret_backend
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        """Drive the tick loop until :meth:`stop` is called."""
        logger.info("sensor-scheduler starting (tick_interval=%.1fs)", self._tick_interval)
        while not self._stop_event.is_set():
            try:
                fired = await self.tick_once()
            except Exception:
                logger.exception("sensor-scheduler: tick failed")
                fired = 0
            if fired:
                logger.info("sensor-scheduler: enqueued %d trigger run(s)", fired)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._tick_interval)
        logger.info("sensor-scheduler stopped")

    async def tick_once(self) -> int:
        """One pass over active sensors; return number of trigger Runs enqueued.

        For each due sensor: lock it (SKIP LOCKED), run its check, stamp
        ``last_check_at`` / ``last_result_json`` / ``last_triggered_at``,
        and if triggered enqueue a PENDING Run on its target pipeline.
        Commits once at the end so a partial tick doesn't half-update.
        """
        now = datetime.now(UTC)
        async with self._factory() as session:
            due = await _load_due_sensors(session, now=now)
            fired = 0
            repo = SensorRepository(session)
            for sensor in due:
                triggered, result_json = await self._run_check(sensor)
                await repo.record_check(
                    sensor, now=now, triggered=triggered, result_json=result_json
                )
                if triggered and sensor.target_pipeline_id is not None:
                    enqueued = await _enqueue_trigger_run(
                        session, sensor=sensor, now=now, result_json=result_json
                    )
                    if enqueued:
                        fired += 1
            if due:
                await session.commit()
            return fired

    async def _run_check(self, sensor: Sensor) -> tuple[bool, dict[str, Any] | None]:
        """Build + run the sensor's ``check()``. Always returns; build
        errors are surfaced through the result_json instead of crashing
        the tick (so one bad config doesn't stop the whole scheduler)."""
        try:
            instance = build_sensor(sensor.type, sensor.config_json or {})
        except ConfigError as e:
            logger.warning("sensor %s (type=%s): config error: %s", sensor.id, sensor.type, e)
            return False, {
                "triggered": False,
                "message": f"config error: {e}",
                "metadata": {"error_class": type(e).__name__},
            }
        try:
            # check_async() is the unified entry point — sync builtins
            # (HTTP, file-landed, …) inherit the default bridge that hops
            # to a thread, async builtins (asset_freshness) read the
            # ContextVar-injected session_factory + workspace_id we set
            # via use_sensor_context.
            async with use_sensor_context(
                session_factory=self._factory,
                workspace_id=sensor.workspace_id,
                last_triggered_at=sensor.last_triggered_at,
                secret_backend=self._secret_backend,
            ):
                result = await instance.check_async()
        except Exception as e:  # the soft-fail contract says this shouldn't
            # happen, but a buggy custom sensor mustn't take down the loop
            logger.warning(
                "sensor %s (type=%s): check raised %s: %s",
                sensor.id,
                sensor.type,
                type(e).__name__,
                e,
            )
            return False, {
                "triggered": False,
                "message": f"check raised: {type(e).__name__}: {e}",
                "metadata": {"error_class": type(e).__name__},
            }
        finally:
            close = getattr(instance, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()
        return result.triggered, {
            "triggered": result.triggered,
            "message": result.message,
            "metadata": dict(result.metadata) if result.metadata else {},
        }

    def stop(self) -> None:
        """Request graceful shutdown — loop exits after current tick."""
        self._stop_event.set()


# --- helpers ----------------------------------------------------------------


async def _load_due_sensors(session: AsyncSession, *, now: datetime) -> list[Sensor]:
    """Active sensors whose next-due time has elapsed.

    A sensor is *due* when ``last_check_at IS NULL`` (never polled) or
    ``last_check_at + max(poll_interval_seconds, MIN) <= now``. The SQL
    only filters active + locks; the per-sensor interval gate runs in
    Python because Postgres column-interval arithmetic needs vendor-
    specific casts that aren't worth it at expected cardinality
    (10s-100s of sensors per replica). Revisit if we ever see thousands
    on a single scheduler.

    ``FOR UPDATE SKIP LOCKED`` (K2 pattern) partitions the work across
    replicas so a sensor can't fire twice per tick even if two scheduler
    processes are calling ``tick_once`` simultaneously.
    """
    stmt = select(Sensor).where(Sensor.is_active.is_(True)).with_for_update(skip_locked=True)
    rows = list((await session.execute(stmt)).scalars().all())

    def _due(s: Sensor) -> bool:
        if s.last_check_at is None:
            return True
        interval = max(_MIN_POLL_INTERVAL_SECONDS, float(s.poll_interval_seconds))
        return s.last_check_at + timedelta(seconds=interval) <= now

    return [s for s in rows if _due(s)]


async def _enqueue_trigger_run(
    session: AsyncSession,
    *,
    sensor: Sensor,
    now: datetime,
    result_json: dict[str, Any] | None,
) -> bool:
    """Insert a PENDING Run on the sensor's target pipeline. Returns True
    iff a run was inserted; False on a missing target / orphaned sensor /
    pipeline with no current version (all are logged + skipped so the tick
    doesn't crash).
    """
    if sensor.target_pipeline_id is None:
        logger.info("sensor %s triggered but has no target_pipeline_id", sensor.id)
        return False
    pipeline = (
        await session.execute(select(Pipeline).where(Pipeline.id == sensor.target_pipeline_id))
    ).scalar_one_or_none()
    if pipeline is None:
        logger.warning(
            "sensor %s target pipeline %s missing — skipping trigger",
            sensor.id,
            sensor.target_pipeline_id,
        )
        return False
    version = (
        await session.execute(
            select(PipelineVersion)
            .where(PipelineVersion.pipeline_id == pipeline.id)
            .where(PipelineVersion.is_current.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if version is None:
        logger.warning(
            "sensor %s target pipeline %s has no current version", sensor.id, pipeline.id
        )
        return False
    session.add(
        Run(
            workspace_id=pipeline.workspace_id,
            pipeline_id=pipeline.id,
            pipeline_version_id=version.id,
            schedule_id=None,
            status=RunStatus.PENDING,
            scheduled_at=now,
            # Stamp the trigger context onto result_json so the downstream
            # pipeline (or the UI) can see what fired it without a join.
            result_json={
                "triggered_by": "sensor",
                "sensor_id": str(sensor.id),
                "sensor_name": sensor.name,
                "sensor_result": result_json or {},
            },
        )
    )
    return True


__all__ = ["SensorScheduler"]
