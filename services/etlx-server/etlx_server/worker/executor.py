"""Execute one already-claimed Run row to completion.

The :class:`RunWorker` poll loop hands off to :class:`RunExecutor` after
:func:`claim_pending_run` has flipped a row to ``running``. From there
the executor is responsible for:

1. Loading the pipeline + its current version + the workspace
   connections it references.
2. Resolving every ``${SECRET:<path>}`` placeholder through the
   :class:`SecretBackend`.
3. Building the core :class:`Pipeline` + connector instances via
   ``etl_plugins.runtime.builder`` (the same code paths YAML-driven
   runs use — so "what the API saved" and "what the worker executes"
   stay in lockstep).
4. Running :meth:`Pipeline.run` in a worker thread (it is synchronous;
   each connector's driver is blocking).
5. Writing the terminal status + counters + duration back to the row.

This slice is batch-only. Stream pipelines have a different lifecycle
(long-running, not "claim → finish") and will be wired into a separate
worker manager in Step 9.4.
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
from etl_plugins.core.pipeline import Pipeline as CorePipeline
from etl_plugins.core.pipeline import RunResult
from etl_plugins.runtime.builder import build_connector, build_pipeline
from etlx_server.db.enums import PipelineMode, RunStatus
from etlx_server.db.models import Pipeline, PipelineTrigger, PipelineVersion, Run
from etlx_server.pipelines.runtime import (
    load_connections_by_name,
    referenced_connection_names,
    resolve_placeholders,
)
from etlx_server.worker.heartbeat import heartbeat_loop
from etlx_server.worker.recorder import RunRecorder, current_run_id

logger = logging.getLogger(__name__)

# Truncate stored error_message to fit reasonable UI display + DB column.
# error_class is a short class name; error_message can be arbitrarily long
# in Python, but we don't want to bloat the runs table.
_MAX_ERROR_MESSAGE_LEN = 2000

# How often to refresh ``runs.heartbeat_at`` during execution. The reaper
# (Step 9.3b) uses this stamp to spot stuck workers; the interval just
# needs to be comfortably below the reaper's idle threshold (default 60s).
_HEARTBEAT_INTERVAL_SECONDS = 10.0

# Safety cap on the call-pipeline trigger chain length (ADR-0029). Cycles are
# already prevented by the visited-set check; this bounds a pathological deep
# fan even in a DAG with no cycles.
_MAX_TRIGGER_CHAIN = 50


class _PipelineBuildError(Exception):
    """Raised by :meth:`RunExecutor._build` so the executor can record the
    failure on the Run row instead of leaking it as a worker crash."""


class RunExecutor:
    """Run lifecycle from "claimed" through terminal state.

    The executor opens a fresh :class:`AsyncSession` for each run so
    the row update does not share a transaction with the worker's
    long-lived poll loop. Connectors are closed in a ``finally`` block
    regardless of success — leaks would tie up driver pools across
    runs.
    """

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        backend: SecretBackend,
        *,
        worker_id: str,
        log_flush_interval_seconds: float | None = None,
    ) -> None:
        """
        Parameters
        ----------
        factory
            Session factory used both for the executor's own row updates and
            for the recorder's periodic flushes. In production this is
            ``make_session_factory(engine)``, which yields a fresh session
            per call — so the recorder's flush task and the executor's
            commit don't share an ``AsyncSession``.
        backend
            Secret backend used to resolve ``${SECRET:...}`` placeholders.
        worker_id
            Identifier stamped on the Run row + recorder lifecycle logs.
        log_flush_interval_seconds
            When set, the :class:`RunRecorder` flushes pending log + metric
            entries every N seconds. Leave ``None`` to flush only on
            executor exit (the default in tests so the shared-session
            fixture doesn't deadlock on concurrent commits).
        """
        self._factory = factory
        self._backend = backend
        self._worker_id = worker_id
        self._log_flush_interval = log_flush_interval_seconds

    async def execute(self, run_id: UUID) -> Run:
        """Execute the run identified by ``run_id``; persist the result.

        The Run row must already be in ``running`` status (i.e.
        previously claimed by :func:`claim_pending_run`). On
        completion (success *or* failure), the row is updated with
        ``status``, ``finished_at``, and any counters/error info.
        """
        async with self._factory() as session:
            run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
            pipeline = (
                await session.execute(select(Pipeline).where(Pipeline.id == run.pipeline_id))
            ).scalar_one()
            version = (
                await session.execute(
                    select(PipelineVersion).where(PipelineVersion.id == run.pipeline_version_id)
                )
            ).scalar_one()

            # Bound logger — events emitted from the executor itself land in
            # ``run_logs`` via the recorder's structlog processor (Step 9.3c).
            log = structlog.get_logger().bind(run_id=str(run.id))

            async with RunRecorder(
                self._factory,
                run.id,
                flush_interval_seconds=self._log_flush_interval,
            ):
                ctx_token = current_run_id.set(run.id)
                try:
                    log.info(
                        "run.build_started",
                        pipeline_id=str(pipeline.id),
                        version=version.version,
                    )
                    try:
                        pipeline_obj, connectors = await self._build(pipeline, version, session)
                    except _PipelineBuildError as e:
                        log.error(
                            "run.build_failed",
                            error_class=type(e).__name__,
                            error=str(e),
                        )
                        _record_failure(run, type(e).__name__, str(e))
                        await session.commit()
                        return run

                    # Connect + run + close must happen in a *single* worker
                    # thread so connector drivers (notably sqlite3) that bind
                    # to a specific thread don't trip on cross-thread reuse.
                    ctx = Context(pipeline_name=pipeline_obj.name, run_id=str(run.id))
                    # Heartbeat task runs on the asyncio main loop with its own
                    # session; while pipeline.run blocks the thread-pool worker,
                    # this keeps ``heartbeat_at`` fresh so the reaper doesn't
                    # mistake an honest long-running run for a zombie.
                    heartbeat_stop = asyncio.Event()
                    heartbeat_task = asyncio.create_task(
                        heartbeat_loop(
                            self._factory,
                            run.id,
                            stop_event=heartbeat_stop,
                            interval_seconds=_HEARTBEAT_INTERVAL_SECONDS,
                        )
                    )
                    try:
                        log.info("run.pipeline_started", pipeline=pipeline_obj.name)
                        result = await asyncio.to_thread(
                            _run_pipeline_in_thread, pipeline_obj, ctx, connectors
                        )
                        _record_success(run, result)
                        log.info(
                            "run.pipeline_succeeded",
                            records_read=result.records_read,
                            records_written=result.records_written,
                            duration_seconds=result.duration_seconds,
                        )
                        # Call-pipeline (ADR-0029): enqueue downstream pipelines
                        # on success, fire-and-forget. Same transaction as the
                        # success write so it's all-or-nothing.
                        await self._trigger_downstream(session, run, log)
                    except Exception as e:
                        # Any exception coming out of ``pipeline.run`` lands here.
                        # The core re-raises after recording metrics, so the
                        # duration_seconds on ``RunResult`` isn't accessible — we
                        # leave it null and log the error.
                        log.error(
                            "run.pipeline_failed",
                            error_class=type(e).__name__,
                            error=str(e),
                        )
                        _record_failure(run, type(e).__name__, str(e))
                    finally:
                        heartbeat_stop.set()
                        with contextlib.suppress(asyncio.CancelledError):
                            await heartbeat_task
                    await session.commit()
                    return run
                finally:
                    current_run_id.reset(ctx_token)

    async def _trigger_downstream(self, session: AsyncSession, run: Run, log: Any) -> None:
        """Enqueue runs of pipelines this one triggers on success (ADR-0029).

        Fire-and-forget. A ``trigger_chain`` carried on each run's
        ``result_json`` records the pipeline lineage so we never re-enqueue a
        pipeline already in the chain (cycle break), and a hard cap bounds depth.
        """
        prior_chain = list((run.result_json or {}).get("trigger_chain") or [])
        if len(prior_chain) >= _MAX_TRIGGER_CHAIN:
            log.warning("run.trigger_chain_capped", depth=len(prior_chain))
            return
        new_chain = [*prior_chain, str(run.pipeline_id)]

        target_ids = (
            (
                await session.execute(
                    select(PipelineTrigger.target_pipeline_id).where(
                        PipelineTrigger.source_pipeline_id == run.pipeline_id
                    )
                )
            )
            .scalars()
            .all()
        )

        for target_id in target_ids:
            if str(target_id) in new_chain:
                continue  # cycle — target already ran upstream in this lineage
            current = (
                await session.execute(
                    select(PipelineVersion).where(
                        PipelineVersion.pipeline_id == target_id,
                        PipelineVersion.is_current.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if current is None:
                log.warning("run.trigger_skipped_no_version", target_pipeline_id=str(target_id))
                continue
            target_pipeline = (
                await session.execute(select(Pipeline).where(Pipeline.id == target_id))
            ).scalar_one()
            session.add(
                Run(
                    workspace_id=target_pipeline.workspace_id,
                    pipeline_id=target_id,
                    pipeline_version_id=current.id,
                    schedule_id=None,
                    triggered_by_user_id=None,
                    status=RunStatus.PENDING,
                    result_json={"triggered_by_run": str(run.id), "trigger_chain": new_chain},
                )
            )
            log.info("run.triggered_downstream", target_pipeline_id=str(target_id))

    async def _build(
        self,
        pipeline: Pipeline,
        version: PipelineVersion,
        session: AsyncSession,
    ) -> tuple[CorePipeline, dict[str, Connector]]:
        """Materialize the stored pipeline into a runnable core ``Pipeline``.

        Raises :class:`_PipelineBuildError` if any step is unrecoverable.
        Successful return guarantees every connection name in the config
        resolves to a constructed :class:`Connector` instance.
        """
        try:
            cfg = PipelineConfig.model_validate(version.config_json)
        except ValidationError as e:
            raise _PipelineBuildError(f"invalid pipeline config: {e.errors()}") from e

        # Step 9.3a is batch-only. ``Pipeline.run`` itself rejects
        # non-batch, but failing here gives a clearer error message and
        # avoids spinning up connectors that won't be used.
        if cfg.mode != PipelineMode.BATCH.value:
            raise _PipelineBuildError(
                f"worker only supports batch pipelines; got mode={cfg.mode!r}"
            )

        names = referenced_connection_names(cfg)
        rows = await load_connections_by_name(
            session, workspace_id=pipeline.workspace_id, names=names
        )
        missing = [n for n in names if n not in rows]
        if missing:
            raise _PipelineBuildError(f"connection(s) not found in workspace: {sorted(missing)}")

        connectors: dict[str, Connector] = {}
        for name in names:
            row = rows[name]
            try:
                resolved = resolve_placeholders(row.config_json, self._backend)
            except SecretError as e:
                raise _PipelineBuildError(
                    f"connection {name!r}: secret resolution failed: {e}"
                ) from e
            if not isinstance(resolved, dict):
                raise _PipelineBuildError(
                    f"connection {name!r}: resolved config is not a JSON object"
                )
            try:
                conn_cfg = ConnectionConfig.model_validate({"type": row.type, **resolved})
            except ValidationError as e:
                raise _PipelineBuildError(
                    f"connection {name!r}: invalid config: {e.errors()}"
                ) from e
            try:
                connectors[name] = build_connector(name, conn_cfg)
            except (ConfigError, RegistryError) as e:
                raise _PipelineBuildError(f"connection {name!r}: {type(e).__name__}: {e}") from e

        try:
            core_pipeline, _ = build_pipeline(cfg, connectors=connectors)
        except ConfigError as e:
            raise _PipelineBuildError(f"pipeline build failed: {e}") from e
        return core_pipeline, connectors


def _record_success(run: Run, result: RunResult) -> None:
    now = datetime.now(UTC)
    run.status = RunStatus.SUCCEEDED
    run.finished_at = now
    run.heartbeat_at = now
    run.records_read = result.records_read
    run.records_written = result.records_written
    run.duration_seconds = result.duration_seconds
    # Keep any pre-existing keys (e.g. ``retry_of`` from a retry) while
    # stamping the core's run_id for cross-system correlation.
    merged: dict[str, Any] = dict(run.result_json or {})
    merged["core_run_id"] = result.run_id
    run.result_json = merged


def _record_failure(run: Run, error_class: str, error_message: str) -> None:
    now = datetime.now(UTC)
    run.status = RunStatus.FAILED
    run.finished_at = now
    run.heartbeat_at = now
    run.error_class = error_class
    run.error_message = error_message[:_MAX_ERROR_MESSAGE_LEN]


def _run_pipeline_in_thread(
    pipeline: CorePipeline,
    context: Context,
    connectors: dict[str, Connector],
) -> RunResult:
    """Open connectors, run the pipeline, close connectors — all in one thread.

    Some drivers (sqlite3 in particular) refuse to be touched from a
    thread other than the one that opened them, so we can't split
    ``connect`` / ``run`` / ``close`` across multiple ``asyncio.to_thread``
    calls — each call may land on a different pool worker.
    """
    for c in connectors.values():
        c.connect()
    try:
        return pipeline.run(context, connectors=connectors)
    finally:
        for c in connectors.values():
            with contextlib.suppress(Exception):
                c.close()


__all__ = ["RunExecutor"]
