"""Stream worker (Step 9.4) — long-running stream pipeline manager.

Batch pipelines are pull-driven: the scheduler enqueues a Run row, the
batch worker claims and finishes it. Stream pipelines invert that —
they run continuously and only stop when the schedule is paused, the
worker shuts down, or the pipeline crashes.

This slice runs as a separate process (``etlx-server stream-worker run``)
and:

1. Scans for active stream :class:`Schedule` rows on a tick.
2. For each one not already running *locally*, creates a Run row with
   ``status=running`` and spawns an :class:`asyncio.Task` that calls
   :meth:`Pipeline.arun_stream`.
3. Maintains heartbeats while the pipeline runs (a separate task per
   in-flight run — same shape as the batch executor's heartbeat loop).
4. On schedule deactivation / deletion or worker shutdown, cancels the
   task and stamps the Run row terminal.

Concurrency: a single stream worker process owns the streams it spawned.
Running two stream-worker processes would race — they'd each try to
start the same schedule. Step 11 operator strengthening will add a
``FOR UPDATE SKIP LOCKED`` claim on the schedule row before spawning
to support multi-replica deployments; for now the single-replica
contract is documented and enforced by deployment.

What the stream worker does NOT do:

* It does not poll the ``runs`` queue (batch worker's job).
* It does not honor ``cron_expr`` on stream schedules (re-arm cron is a
  future enhancement — currently a stream schedule with ``cron_expr``
  is treated identically to one with ``cron_expr=NULL``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from etl_plugins.config.models import ConnectionConfig, PipelineConfig
from etl_plugins.config.secrets import SecretBackend
from etl_plugins.core.connector import Connector
from etl_plugins.core.context import Context
from etl_plugins.core.exceptions import ConfigError, RegistryError, SecretError
from etl_plugins.runtime.builder import build_connector, build_pipeline
from etlx_server.db.enums import PipelineMode, RunStatus
from etlx_server.db.models import Pipeline, PipelineVersion, Run, Schedule
from etlx_server.pipelines.runtime import (
    load_connections_by_name,
    referenced_connection_names,
    resolve_placeholders,
)
from etlx_server.worker.heartbeat import heartbeat_loop
from etlx_server.worker.recorder import RunRecorder, current_run_id

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL_SECONDS = 10.0
_MAX_ERROR_MESSAGE_LEN = 2000


class StreamWorker:
    """Manages the lifecycle of stream pipelines for a workspace fleet.

    Single-replica today: scan → spawn → maintain. On stop, cancel every
    in-flight stream and stamp the Run rows terminal so the UI doesn't
    show ghost ``running`` rows after the operator restarts the process.
    """

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        backend: SecretBackend,
        *,
        worker_id: str,
        tick_interval_seconds: float = 5.0,
        log_flush_interval_seconds: float | None = 2.0,
    ) -> None:
        self._factory = factory
        self._backend = backend
        self._worker_id = worker_id
        self._tick_interval = tick_interval_seconds
        self._log_flush_interval = log_flush_interval_seconds
        self._stop_event = asyncio.Event()
        # schedule_id -> the asyncio.Task running its stream pipeline.
        self._inflight: dict[UUID, asyncio.Task[None]] = {}
        # schedule_id -> ``updated_at`` of the schedule at the time its
        # stream task finished (any terminal status). Used to skip
        # re-spawn until the user actually changes the schedule, so a
        # mis-configured pipeline doesn't generate a Run row per tick.
        self._terminated_at_update: dict[UUID, datetime] = {}

    async def run(self) -> None:
        """Drive the scanner loop until :meth:`stop` is called.

        On exit, every in-flight stream is cancelled and awaited so its
        Run row reaches a terminal state before the process returns.
        """
        logger.info(
            "stream-worker %s starting (tick_interval=%.1fs)",
            self._worker_id,
            self._tick_interval,
        )
        try:
            while not self._stop_event.is_set():
                try:
                    await self._tick()
                except Exception:
                    logger.exception("stream-worker %s: tick failed", self._worker_id)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self._tick_interval)
        finally:
            await self._shutdown_inflight()
        logger.info("stream-worker %s stopped", self._worker_id)

    def stop(self) -> None:
        """Request graceful shutdown — loop exits after the current tick."""
        self._stop_event.set()

    async def _tick(self) -> None:
        """One scan over active stream schedules; spawn missing tasks +
        prune tasks whose schedule no longer applies."""
        async with self._factory() as session:
            active = await _load_active_stream_schedules(session)
        active_ids = {s.id for s in active}

        # Stop tasks whose schedule was deactivated or deleted. Also clear
        # the "don't re-spawn" marker so re-activating triggers a fresh
        # spawn next tick.
        gone = [sid for sid in self._inflight if sid not in active_ids]
        for sid in gone:
            await self._stop_schedule(sid, reason="schedule no longer active")
        retired = [sid for sid in self._terminated_at_update if sid not in active_ids]
        for sid in retired:
            self._terminated_at_update.pop(sid, None)

        # Spawn tasks for newly active schedules.
        for schedule in active:
            if schedule.id in self._inflight:
                continue
            terminated_update = self._terminated_at_update.get(schedule.id)
            if terminated_update is not None and schedule.updated_at == terminated_update:
                # Previous run finished without success and the schedule
                # hasn't been touched since. Skip — user has to edit the
                # schedule (toggle, cron change, etc.) to retry.
                continue
            self._terminated_at_update.pop(schedule.id, None)
            run_id = await self._start_schedule(schedule)
            if run_id is not None:
                logger.info(
                    "stream-worker %s: started schedule %s as run %s",
                    self._worker_id,
                    schedule.id,
                    run_id,
                )

    async def _start_schedule(self, schedule: Schedule) -> UUID | None:
        """Insert a Run row in ``running`` status and spawn its asyncio task."""
        async with self._factory() as session:
            pipeline = (
                await session.execute(select(Pipeline).where(Pipeline.id == schedule.pipeline_id))
            ).scalar_one_or_none()
            if pipeline is None:
                logger.warning(
                    "stream-worker %s: schedule %s references missing pipeline",
                    self._worker_id,
                    schedule.id,
                )
                return None
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
                    "stream-worker %s: pipeline %s has no current version",
                    self._worker_id,
                    pipeline.id,
                )
                return None
            now = datetime.now(UTC)
            run = Run(
                workspace_id=pipeline.workspace_id,
                pipeline_id=pipeline.id,
                pipeline_version_id=version.id,
                schedule_id=schedule.id,
                status=RunStatus.RUNNING,
                worker_id=self._worker_id,
                scheduled_at=now,
                started_at=now,
                heartbeat_at=now,
            )
            session.add(run)
            await session.flush()
            await session.commit()
            run_id = run.id

        task = asyncio.create_task(
            self._drive_stream(run_id),
            name=f"stream-run-{run_id}",
        )
        self._inflight[schedule.id] = task
        schedule_updated_at = schedule.updated_at

        def _on_done(_t: asyncio.Task[None]) -> None:
            self._inflight.pop(schedule.id, None)
            # Track the schedule.updated_at value seen at termination time
            # so the next tick can decide whether the user has changed
            # anything (-> retry) or not (-> leave alone).
            self._terminated_at_update[schedule.id] = schedule_updated_at

        task.add_done_callback(_on_done)
        return run_id

    async def _stop_schedule(self, schedule_id: UUID, *, reason: str) -> None:
        task = self._inflight.pop(schedule_id, None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        logger.info(
            "stream-worker %s: stopped schedule %s (%s)",
            self._worker_id,
            schedule_id,
            reason,
        )

    async def _shutdown_inflight(self) -> None:
        ids = list(self._inflight.keys())
        for sid in ids:
            await self._stop_schedule(sid, reason="worker shutdown")

    async def _drive_stream(self, run_id: UUID) -> None:
        """Materialize the pipeline + run :meth:`Pipeline.arun_stream`.

        Failures are written to the Run row's ``error_class`` /
        ``error_message`` so the UI shows them; the task itself never
        re-raises (the asyncio task callback would otherwise log an
        ugly traceback at the loop level).
        """
        async with self._factory() as session:
            pipeline_obj, connectors, run_obj = await self._build_or_fail(session, run_id)

        if run_obj is None:
            # _build_or_fail already stamped the Run row terminal.
            return

        log = structlog.get_logger().bind(run_id=str(run_id))
        async with RunRecorder(
            self._factory,
            run_id,
            flush_interval_seconds=self._log_flush_interval,
        ):
            ctx_token = current_run_id.set(run_id)
            heartbeat_stop = asyncio.Event()
            heartbeat_task = asyncio.create_task(
                heartbeat_loop(
                    self._factory,
                    run_id,
                    stop_event=heartbeat_stop,
                    interval_seconds=_HEARTBEAT_INTERVAL_SECONDS,
                ),
                name=f"stream-heartbeat-{run_id}",
            )
            try:
                log.info("stream.started", pipeline=pipeline_obj.name)
                # Open all connectors before subscribing — same contract as
                # the batch path. Stream connectors typically lazy-connect on
                # subscribe(), but be explicit.
                for c in connectors.values():
                    if hasattr(c, "connect") and callable(c.connect):
                        await asyncio.to_thread(c.connect)
                ctx = Context(pipeline_name=pipeline_obj.name, run_id=str(run_id))
                try:
                    result = await pipeline_obj.arun_stream(ctx, connectors=connectors)
                    await self._record_terminal(
                        run_id,
                        status=RunStatus.SUCCEEDED,
                        records_read=result.records_read,
                        records_written=result.records_written,
                        duration_seconds=result.duration_seconds,
                    )
                    log.info("stream.exhausted", records_read=result.records_read)
                except asyncio.CancelledError:
                    await self._record_terminal(
                        run_id,
                        status=RunStatus.CANCELLED,
                        error_class="StreamCancelled",
                        error_message="worker stopped the stream",
                    )
                    log.warning("stream.cancelled")
                    raise
                except Exception as e:
                    await self._record_terminal(
                        run_id,
                        status=RunStatus.FAILED,
                        error_class=type(e).__name__,
                        error_message=str(e),
                    )
                    log.error(
                        "stream.failed",
                        error_class=type(e).__name__,
                        error=str(e),
                    )
                finally:
                    for c in connectors.values():
                        if hasattr(c, "close") and callable(c.close):
                            with contextlib.suppress(Exception):
                                await asyncio.to_thread(c.close)
            finally:
                heartbeat_stop.set()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
                current_run_id.reset(ctx_token)

    async def _build_or_fail(
        self, session: AsyncSession, run_id: UUID
    ) -> tuple[Any, dict[str, Connector], Run | None]:
        """Build the core stream pipeline + connector instances.

        Stamps the Run row failed on any build error and returns
        ``(_, _, None)`` so the caller skips the run-loop.
        """
        run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
        pipeline_row = (
            await session.execute(select(Pipeline).where(Pipeline.id == run.pipeline_id))
        ).scalar_one()
        version_row = (
            await session.execute(
                select(PipelineVersion).where(PipelineVersion.id == run.pipeline_version_id)
            )
        ).scalar_one()

        try:
            cfg = PipelineConfig.model_validate(version_row.config_json)
        except ValidationError as e:
            await self._record_terminal(
                run_id,
                status=RunStatus.FAILED,
                error_class="ValidationError",
                error_message=f"invalid pipeline config: {e.errors()}",
            )
            return (None, {}, None)

        if cfg.mode != PipelineMode.STREAM.value:
            await self._record_terminal(
                run_id,
                status=RunStatus.FAILED,
                error_class="ModeMismatch",
                error_message=(f"stream worker received non-stream pipeline (mode={cfg.mode!r})"),
            )
            return (None, {}, None)

        names = referenced_connection_names(cfg)
        rows = await load_connections_by_name(
            session, workspace_id=pipeline_row.workspace_id, names=names
        )
        missing = [n for n in names if n not in rows]
        if missing:
            await self._record_terminal(
                run_id,
                status=RunStatus.FAILED,
                error_class="MissingConnections",
                error_message=f"connection(s) not found: {sorted(missing)}",
            )
            return (None, {}, None)

        connectors: dict[str, Connector] = {}
        for name in names:
            row = rows[name]
            try:
                resolved = resolve_placeholders(row.config_json, self._backend)
                conn_cfg = ConnectionConfig.model_validate({"type": row.type, **resolved})
                connectors[name] = build_connector(name, conn_cfg)
            except (SecretError, ValidationError, ConfigError, RegistryError) as e:
                await self._record_terminal(
                    run_id,
                    status=RunStatus.FAILED,
                    error_class=type(e).__name__,
                    error_message=f"connection {name!r}: {e}",
                )
                return (None, {}, None)

        try:
            core_pipeline, _ = build_pipeline(cfg, connectors=connectors)
        except ConfigError as e:
            await self._record_terminal(
                run_id,
                status=RunStatus.FAILED,
                error_class=type(e).__name__,
                error_message=f"pipeline build failed: {e}",
            )
            return (None, {}, None)

        return core_pipeline, connectors, run

    async def _record_terminal(
        self,
        run_id: UUID,
        *,
        status: RunStatus,
        records_read: int = 0,
        records_written: int = 0,
        duration_seconds: float | None = None,
        error_class: str | None = None,
        error_message: str | None = None,
    ) -> None:
        async with self._factory() as session:
            run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
            if run is None:
                return
            now = datetime.now(UTC)
            run.status = status
            run.finished_at = now
            run.heartbeat_at = now
            if records_read:
                run.records_read = records_read
            if records_written:
                run.records_written = records_written
            if duration_seconds is not None:
                run.duration_seconds = duration_seconds
            if error_class is not None:
                run.error_class = error_class
            if error_message is not None:
                run.error_message = error_message[:_MAX_ERROR_MESSAGE_LEN]
            await session.commit()


async def _load_active_stream_schedules(session: AsyncSession) -> list[Schedule]:
    """Active schedules with ``mode=stream``. Stream pipelines run while
    their schedule is active regardless of ``cron_expr``."""
    stmt = (
        select(Schedule)
        .where(Schedule.is_active.is_(True))
        .where(Schedule.mode == PipelineMode.STREAM)
    )
    return list((await session.execute(stmt)).scalars().all())


__all__ = ["StreamWorker"]
