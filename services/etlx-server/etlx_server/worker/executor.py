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
import functools
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from etl_plugins.config.models import ConnectionConfig, PipelineConfig
from etl_plugins.config.secrets import SecretBackend
from etl_plugins.config.variables import resolve_config_variables
from etl_plugins.core.asset import AssetLineage
from etl_plugins.core.connector import Connector
from etl_plugins.core.context import Context
from etl_plugins.core.exceptions import ConfigError, RegistryError, SecretError
from etl_plugins.core.pipeline import Pipeline as CorePipeline
from etl_plugins.core.pipeline import RunResult, Task
from etl_plugins.runtime.builder import build_connector, build_pipeline
from etlx_server.audit.service import AuditService
from etlx_server.db.enums import PipelineMode, RunStatus
from etlx_server.db.models import Pipeline, PipelineTrigger, PipelineVersion, Run
from etlx_server.node_runs import NodeRunRepository, NodeSpec
from etlx_server.pipelines.runtime import (
    load_connections_by_name,
    referenced_connection_names,
    resolve_placeholders,
)
from etlx_server.variables.repository import WorkspaceVariableRepository
from etlx_server.worker.heartbeat import heartbeat_loop
from etlx_server.worker.node_graph import (
    NODE_FAILED,
    NODE_SUCCEEDED,
    NodeOutcome,
    execute_graph_nodes_concurrent,
)
from etlx_server.worker.recorder import RunRecorder, current_run_id

logger = logging.getLogger(__name__)


class _NodeExecutionError(Exception):
    """Raised when a node-level graph run had a failing node (routes the run to failed)."""


class _RunCancelledError(Exception):
    """Phase P (2026-05-28) — sentinel for user-requested cancellation
    detected mid-run. Branches the executor's except-handling away from
    ``_record_failure`` (which would write a misleading error_class /
    error_message) to ``_record_cancelled`` (status=cancelled, no error
    fields). Never bubbles past the executor's try/except."""


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
        # Node-level execution (ADR-0041, H2b/H2c). _prepare flips ``_node_level_active``
        # when the pipeline opts in + is a graph; ``execute`` then runs the concurrent
        # wave executor (H2c) directly and writes node_runs from the outcomes.
        self._node_level_active = False
        self._node_level_task: Task | None = None
        self._node_level_conn_cfgs: dict[str, ConnectionConfig] = {}
        self._node_level_run_id: str = ""
        self._node_outcomes: list[NodeOutcome] | None = None
        self._node_deps: dict[str, list[str]] = {}

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
                    # Backfill (ADR-0039): a run can carry a cursor range in
                    # result_json.backfill, driving an incremental read.
                    backfill = (run.result_json or {}).get("backfill") or {}
                    try:
                        runner, run_name = await self._prepare(
                            pipeline,
                            version,
                            session,
                            run.id,
                            cursor_from=backfill.get("cursor_from"),
                            cursor_to=backfill.get("cursor_to"),
                        )
                    except _PipelineBuildError as e:
                        log.error(
                            "run.build_failed",
                            error_class=type(e).__name__,
                            error=str(e),
                        )
                        _record_failure(run, type(e).__name__, str(e))
                        await session.commit()
                        return run

                    # Heartbeat task runs on the asyncio main loop with its own
                    # session; while the run blocks the thread-pool worker, this
                    # keeps ``heartbeat_at`` fresh so the reaper doesn't mistake
                    # an honest long-running run for a zombie. The cancel_event
                    # (Phase P, 2026-05-28) lets the heartbeat poll
                    # ``runs.cancel_requested_at`` and signal the graph executor
                    # to bail out at the next wave boundary if cancel was
                    # requested via POST /runs/{rid}/cancel.
                    heartbeat_stop = asyncio.Event()
                    cancel_event = threading.Event()
                    heartbeat_task = asyncio.create_task(
                        heartbeat_loop(
                            self._factory,
                            run.id,
                            stop_event=heartbeat_stop,
                            interval_seconds=_HEARTBEAT_INTERVAL_SECONDS,
                            cancel_event=cancel_event,
                        )
                    )
                    try:
                        log.info("run.pipeline_started", pipeline=run_name)
                        if self._node_level_active:
                            # Per-node concurrent path (ADR-0041 H2c) + live
                            # node_runs updates (H3a). Pre-insert PENDING rows in
                            # a fresh session so they're visible before execution
                            # starts; callbacks commit per-node status mid-run.
                            assert self._node_level_task is not None
                            outcomes = await self._run_node_level(
                                run.id, run_name, cancel_event=cancel_event
                            )
                            self._node_outcomes = outcomes
                            failures = [o for o in outcomes if o.status == NODE_FAILED]
                            if cancel_event.is_set() and not failures:
                                # User-requested cancel landed cleanly (no node
                                # failed before the wave-boundary check). Raise
                                # the sentinel so the except below maps it to
                                # _record_cancelled instead of _record_failure
                                # (a cancel isn't an error from the operator's
                                # perspective — see the rationale on
                                # _record_cancelled).
                                raise _RunCancelledError("cancelled by user")
                            if failures:
                                f = failures[0]
                                raise _NodeExecutionError(
                                    f"node {f.node_id!r} failed: {f.error_class}: {f.error_message}"
                                )
                            result = RunResult(
                                run_id=self._node_level_run_id,
                                pipeline_name=run_name,
                                success=True,
                                records_read=sum(
                                    o.records_read for o in outcomes if o.status == NODE_SUCCEEDED
                                ),
                                records_written=sum(
                                    o.records_written
                                    for o in outcomes
                                    if o.status == NODE_SUCCEEDED
                                ),
                            )
                        else:
                            result = await asyncio.to_thread(runner)
                        _record_success(run, result)
                        log.info(
                            "run.pipeline_succeeded",
                            records_read=result.records_read,
                            records_written=result.records_written,
                            duration_seconds=result.duration_seconds,
                        )
                        # Lineage (ADR-0036/0037): record the assets this run
                        # materialized + edges, then auto-trigger downstream
                        # pipelines that consume them. Best-effort — a catalog
                        # hiccup must not flip a successful run to failed.
                        lineage = self._lineage_for(version, log)
                        if lineage is not None:
                            await self._persist_lineage(session, run, result, lineage, log)
                            await self._persist_column_lineage(session, run, version, lineage, log)
                            await self._trigger_asset_consumers(session, run, lineage, log)
                        # Call-pipeline (ADR-0029): enqueue downstream pipelines
                        # on success, fire-and-forget. Same transaction as the
                        # success write so it's all-or-nothing.
                        await self._trigger_downstream(session, run, log)
                    except _RunCancelledError:
                        # Phase P (2026-05-28) — user-requested cancel. Not an
                        # error; suppress the failure log + record_failure
                        # path and write status=cancelled cleanly.
                        log.info(
                            "run.pipeline_cancelled",
                            node_outcomes=len(self._node_outcomes or []),
                        )
                        _record_cancelled(run)
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
                    # node_runs are written live by ``_run_node_level`` (H3a) —
                    # nothing more to record at the end.
                    # Phase U (2026-05-28): one audit row per
                    # SQL-executing / Python-executing node so the
                    # workspace audit trail records WHAT operations
                    # the run touched, not just THAT it ran. Same
                    # session as the status write so it's all-or-
                    # nothing — a rolled-back run never leaves a "ran
                    # SQL X" trace.
                    try:
                        await self._record_data_operations(session, run, version, log)
                    except Exception:
                        log.exception("run.audit_data_ops_failed")
                    await session.commit()
                    return run
                finally:
                    current_run_id.reset(ctx_token)

    async def _run_node_level(
        self,
        run_id: UUID,
        run_name: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> list[NodeOutcome]:
        """Execute the node-level path with live ``node_runs`` updates (ADR-0041 H3a).

        Pre-inserts PENDING ``node_runs`` (with deps) in a fresh session so a
        polling UI sees the DAG shape before execution starts. Then runs the
        wave-based concurrent executor (H2c) with callbacks that commit per-node
        status mid-run — running → succeeded/failed/cancelled — each in its own
        session so progress is observable immediately rather than only at the
        end of the run.
        """
        assert self._node_level_task is not None
        repo = NodeRunRepository()
        specs = [
            NodeSpec(
                node_id=n.id,
                kind=n.kind,
                depends_on=self._node_deps.get(n.id, []),
            )
            for n in self._node_level_task.graph_nodes
        ]
        async with self._factory() as init_session:
            created = await repo.create_for_run(init_session, run_id, specs)
            await init_session.commit()
        node_run_id_by_id = {nr.node_id: nr.id for nr in created}

        worker_id = self._worker_id
        factory = self._factory
        # Serialize concurrent callbacks: in production fresh sessions are
        # independent, but tests share one session across factory() calls and
        # would hit "Session is already flushing" when two gather'd waves
        # finish simultaneously. Lock is cheap (DB I/O fast vs to_thread work).
        db_lock = asyncio.Lock()

        async def _on_start(nid: str) -> None:
            async with db_lock, factory() as s:
                await repo.set_running(s, node_run_id=node_run_id_by_id[nid], worker_id=worker_id)
                await s.commit()

        async def _on_finish(outcome: NodeOutcome) -> None:
            nr_id = node_run_id_by_id[outcome.node_id]
            async with db_lock, factory() as s:
                if outcome.status == NODE_SUCCEEDED:
                    await repo.set_succeeded(
                        s,
                        node_run_id=nr_id,
                        records_read=outcome.records_read,
                        records_written=outcome.records_written,
                    )
                elif outcome.status == NODE_FAILED:
                    await repo.set_failed(
                        s,
                        node_run_id=nr_id,
                        error_class=outcome.error_class or "",
                        error_message=outcome.error_message or "",
                    )
                else:  # NODE_SKIPPED → cancelled (no SKIPPED enum)
                    await repo.set_cancelled(s, node_run_id=nr_id)
                await s.commit()

        return await execute_graph_nodes_concurrent(
            self._node_level_task,
            self._node_level_conn_cfgs,
            on_node_start=_on_start,
            on_node_finish=_on_finish,
            cancel_event=cancel_event,
        )

    def _lineage_for(self, version: PipelineVersion, log: Any) -> AssetLineage | None:
        """Derive the run's static asset lineage from config. ``None`` on any
        parse/derive error (best-effort — lineage never fails a run)."""
        from etl_plugins.runtime.lineage import derive_lineage

        try:
            return derive_lineage(PipelineConfig.model_validate(version.config_json))
        except Exception as e:
            log.warning("run.lineage_derive_failed", error_class=type(e).__name__, error=str(e))
            return None

    async def _persist_lineage(
        self,
        session: AsyncSession,
        run: Run,
        result: RunResult,
        lineage: AssetLineage,
        log: Any,
    ) -> None:
        """Record the run's assets + edges + a materialization per output
        (ADR-0036). Best-effort: never fails the run."""
        from etlx_server.assets.repository import AssetRepository

        if not lineage.inputs and not lineage.outputs:
            return
        try:
            await AssetRepository(session).persist_run_lineage(
                workspace_id=run.workspace_id,
                run_id=run.id,
                lineage=lineage,
                records_written=result.records_written,
                kinds=lineage.kinds,
            )
        except Exception as e:
            log.warning("run.lineage_persist_failed", error_class=type(e).__name__, error=str(e))

    async def _persist_column_lineage(
        self,
        session: AsyncSession,
        run: Run,
        version: PipelineVersion,
        lineage: AssetLineage,
        log: Any,
    ) -> None:
        """Record per-column lineage for this run's output assets (ADR-0041 J2).

        Best-effort: a column-lineage glitch (parse error, unknown transform,
        etc.) never flips a successful run to failed. Runs *after*
        :meth:`_persist_lineage` so the asset rows exist for the repo to
        reference by key.
        """
        from etl_plugins.runtime.column_lineage import derive_column_lineage
        from etlx_server.assets.repository import AssetRepository

        if not lineage.outputs:
            return
        try:
            col_lineage = derive_column_lineage(PipelineConfig.model_validate(version.config_json))
        except Exception as e:  # parse or unsupported shape — fall back silently
            log.warning(
                "run.column_lineage_derive_failed",
                error_class=type(e).__name__,
                error=str(e),
            )
            return
        try:
            await AssetRepository(session).persist_run_column_lineage(
                workspace_id=run.workspace_id,
                lineage=col_lineage,
                output_keys=list(lineage.outputs),
            )
        except Exception as e:
            log.warning(
                "run.column_lineage_persist_failed",
                error_class=type(e).__name__,
                error=str(e),
            )

    async def _trigger_asset_consumers(
        self, session: AsyncSession, run: Run, lineage: AssetLineage, log: Any
    ) -> None:
        """Auto-enqueue runs of opt-in pipelines whose inputs match the assets
        this run just materialized (ADR-0037 — asset-driven orchestration).

        Only batch pipelines with ``auto_materialize: true`` in their current
        version are considered. A ``trigger_chain`` on ``result_json`` breaks
        cycles (a pipeline already upstream in this lineage isn't re-enqueued)
        and a depth cap bounds runaway fan. Best-effort — never fails the run.
        """
        from etl_plugins.runtime.lineage import derive_lineage

        output_keys = {str(k) for k in lineage.outputs}
        if not output_keys:
            return
        prior_chain = list((run.result_json or {}).get("trigger_chain") or [])
        if len(prior_chain) >= _MAX_TRIGGER_CHAIN:
            log.warning("run.trigger_chain_capped", depth=len(prior_chain))
            return
        new_chain = [*prior_chain, str(run.pipeline_id)]

        try:
            rows = (
                await session.execute(
                    select(
                        Pipeline.id,
                        Pipeline.workspace_id,
                        PipelineVersion.id,
                        PipelineVersion.config_json,
                    )
                    .join(PipelineVersion, PipelineVersion.pipeline_id == Pipeline.id)
                    .where(
                        Pipeline.workspace_id == run.workspace_id,
                        PipelineVersion.is_current.is_(True),
                    )
                )
            ).all()
        except Exception as e:
            log.warning("run.asset_trigger_failed", error_class=type(e).__name__, error=str(e))
            return

        for pipeline_id, ws_id, version_id, config_json in rows:
            if str(pipeline_id) in new_chain:
                continue  # cycle, or the pipeline that just ran
            cfg_dict = config_json or {}
            if not cfg_dict.get("auto_materialize"):
                continue
            try:
                cfg = PipelineConfig.model_validate(cfg_dict)
            except ValidationError:
                continue
            if cfg.mode != PipelineMode.BATCH.value:
                continue  # stream pipelines aren't driven by the runs queue
            matched = {str(k) for k in derive_lineage(cfg).inputs} & output_keys
            if not matched:
                continue
            session.add(
                Run(
                    workspace_id=ws_id,
                    pipeline_id=pipeline_id,
                    pipeline_version_id=version_id,
                    schedule_id=None,
                    triggered_by_user_id=None,
                    status=RunStatus.PENDING,
                    result_json={
                        "triggered_by_run": str(run.id),
                        "trigger_chain": new_chain,
                        "triggered_by_assets": sorted(matched),
                    },
                )
            )
            log.info(
                "run.triggered_by_asset",
                target_pipeline_id=str(pipeline_id),
                assets=sorted(matched),
            )

    async def _record_data_operations(
        self,
        session: AsyncSession,
        run: Run,
        version: PipelineVersion,
        log: Any,
    ) -> None:
        """Write one ``audit_log`` row per SQL-executing / Python-executing
        node in this run (Phase U, 2026-05-28).

        Why: the existing workspace audit trail records *control plane*
        events (pipeline.create, run.cancel, …). For compliance — "who
        ran which SQL against prod?" / "what Python ran on customer
        data?" — we also need *data plane* events. Recording the
        operations alongside the same audit table keeps the timeline
        unified (one source of truth for "what happened in this
        workspace"), and the existing UI renders the new action types
        with no schema change.

        Coverage:
          * ``sql_exec`` graph nodes (ADR-0042) — standalone SQL.
          * ``transform: sql_exec`` in legacy linear configs (ADR-0035
            pre-load action).
          * ``transform: python`` — user-supplied ``module:function``.
          * ``transform: custom_python`` — inline Python in the
            browser (ADR-0041 I2).

        Both SUCCEEDED and FAILED nodes are recorded: a failed SQL may
        still have made partial changes before the error fired, and
        regulators want the attempted operation either way. Node-level
        outcomes are only available for node_level runs; legacy
        non-node-level runs record every operation the config would
        have executed (we don't have per-node success/failure for
        them).

        Best-effort: errors here are logged + swallowed by the caller
        — the audit trail of the operations must not flip a successful
        run to failed.
        """
        import hashlib

        try:
            cfg = PipelineConfig.model_validate(version.config_json)
        except Exception as e:
            log.warning("run.audit_data_ops_parse_failed", error=str(e))
            return

        # For node-level runs we know which nodes actually executed.
        # For non-node-level runs (or older runs without recorded
        # outcomes), record every operation in the config — the run
        # ran end-to-end, so they all executed.
        executed_node_ids: set[str] | None = None
        if self._node_outcomes is not None:
            from etlx_server.worker.node_graph import NODE_SKIPPED

            executed_node_ids = {o.node_id for o in self._node_outcomes if o.status != NODE_SKIPPED}

        audit = AuditService(session)

        def _hash(text: str) -> str:
            """Stable short fingerprint for SQL / code text. Lets the
            UI dedupe identical statements + makes "same query as
            yesterday" answerable without storing the full text in
            metadata."""
            return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]

        async def _record_sql(
            *, node_id: str, kind: str, connection: str | None, statement: str
        ) -> None:
            await audit.record(
                actor_user_id=run.triggered_by_user_id,
                workspace_id=run.workspace_id,
                action="run.sql_executed",
                resource_type="run",
                resource_id=str(run.id),
                after={
                    "node_id": node_id,
                    "kind": kind,
                    "connection": connection,
                    # Truncated to keep one node's SQL bounded; full
                    # text already lives in version.config_json for
                    # forensic deep-dive.
                    "statement": statement[:2000],
                    "statement_truncated": len(statement) > 2000,
                    "statement_hash": _hash(statement),
                },
            )

        async def _record_python(*, node_id: str, kind: str, **fields: Any) -> None:
            await audit.record(
                actor_user_id=run.triggered_by_user_id,
                workspace_id=run.workspace_id,
                action="run.python_executed",
                resource_type="run",
                resource_id=str(run.id),
                after={"node_id": node_id, "kind": kind, **fields},
            )

        # ── graph shape (ADR-0030 + ADR-0042) ──────────────────────
        if cfg.graph is not None:
            for node in cfg.graph.nodes:
                if executed_node_ids is not None and node.id not in executed_node_ids:
                    continue
                if node.type == "sql_exec":
                    # Standalone SQL node (ADR-0042 follow-up). Both
                    # connection + statement are required by the
                    # GraphNodeConfig validator, so non-null.
                    await _record_sql(
                        node_id=node.id,
                        kind="sql_exec",
                        connection=node.connection,
                        statement=node.statement or "",
                    )
                elif node.type == "transform" and node.transform is not None:
                    tdump = node.transform.model_dump()
                    ttype = tdump.get("type")
                    if ttype == "sql_exec":
                        await _record_sql(
                            node_id=node.id,
                            kind="transform:sql_exec",
                            connection=tdump.get("connection"),
                            statement=tdump.get("statement") or "",
                        )
                    elif ttype == "python":
                        await _record_python(
                            node_id=node.id,
                            kind="transform:python",
                            module_function=tdump.get("callable") or tdump.get("function") or "",
                        )
                    elif ttype == "custom_python":
                        code = tdump.get("code") or ""
                        await _record_python(
                            node_id=node.id,
                            kind="transform:custom_python",
                            first_line=code.split("\n", 1)[0][:200],
                            lines=code.count("\n") + 1 if code else 0,
                            size_bytes=len(code),
                            code_hash=_hash(code),
                        )
            return

        # ── linear / task-DAG shape ────────────────────────────────
        # Non-node-level — no per-node outcomes. Iterate every
        # operation in every task; we know the run completed (the
        # caller only invokes us after a success path) so all are
        # considered "executed". For the failure path we still
        # record — the FIRST failing step's prior steps DID run.
        for task in cfg.effective_tasks():
            # Linear configs put sql_exec inside transforms; the
            # builder lifts them into task.pre_sql (ADR-0035) but
            # the config still carries them in ``transforms`` at the
            # API layer. Iterate transforms verbatim.
            for tcfg in task.transforms:
                tdump = tcfg.model_dump()
                ttype = tdump.get("type")
                node_id = task.name  # no node id in linear shape — task name is the closest analog
                if ttype == "sql_exec":
                    await _record_sql(
                        node_id=node_id,
                        kind="transform:sql_exec",
                        connection=tdump.get("connection"),
                        statement=tdump.get("statement") or "",
                    )
                elif ttype == "python":
                    await _record_python(
                        node_id=node_id,
                        kind="transform:python",
                        module_function=tdump.get("callable") or tdump.get("function") or "",
                    )
                elif ttype == "custom_python":
                    code = tdump.get("code") or ""
                    await _record_python(
                        node_id=node_id,
                        kind="transform:custom_python",
                        first_line=code.split("\n", 1)[0][:200],
                        lines=code.count("\n") + 1 if code else 0,
                        size_bytes=len(code),
                        code_hash=_hash(code),
                    )

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

    async def _prepare(
        self,
        pipeline: Pipeline,
        version: PipelineVersion,
        session: AsyncSession,
        run_id: UUID,
        *,
        cursor_from: Any = None,
        cursor_to: Any = None,
    ) -> tuple[Callable[[], RunResult], str]:
        """Build a thread-callable that runs the pipeline in-process.

        Returns ``(runner, name)``. Raises :class:`_PipelineBuildError` for any
        unrecoverable build/resolution problem (recorded as a failed run).
        ``cursor_from`` / ``cursor_to`` drive a backfill over the source's
        ``cursor_column`` (ADR-0039).
        """
        # Resolve ${var.name}: workspace globals merged under the pipeline's local
        # variables block (locals win), then the config validates (ADR-0041, V2).
        global_vars = await WorkspaceVariableRepository(session).as_dict(
            workspace_id=pipeline.workspace_id
        )
        try:
            cfg_dict = resolve_config_variables(version.config_json, extra=global_vars)
            cfg = PipelineConfig.model_validate(cfg_dict)
        except ConfigError as e:
            raise _PipelineBuildError(f"variable resolution failed: {e}") from e
        except ValidationError as e:
            raise _PipelineBuildError(f"invalid pipeline config: {e.errors()}") from e
        if cfg.mode != PipelineMode.BATCH.value:
            raise _PipelineBuildError(
                f"worker only supports batch pipelines; got mode={cfg.mode!r}"
            )

        conn_cfgs = await self._resolve_connection_configs(pipeline, cfg, session)

        # Node-level execution (ADR-0041, H2c, opt-in): build the graph Task with
        # **no factory** so sink.name stays a plain connection name — the
        # concurrent executor mints + connects + closes connectors per node in
        # its own thread (thread-bound drivers safe). Branches BEFORE the
        # factory-based whole-graph build below.
        if cfg.node_level and cfg.graph is not None:
            return self._prepare_node_level(cfg, conn_cfgs, run_id)

        # Build connector instances + a core Pipeline (in-process execution).
        connectors: dict[str, Connector] = {}
        for name, conn_cfg in conn_cfgs.items():
            try:
                connectors[name] = build_connector(name, conn_cfg)
            except (ConfigError, RegistryError) as e:
                raise _PipelineBuildError(f"connection {name!r}: {type(e).__name__}: {e}") from e

        # Factory for dedicated sink connections: a pipeline that reads from and
        # writes to the same connection needs two physical connections, or the
        # streaming read cursor and the write deadlock on one shared connection.
        def _factory(name: str) -> Connector:
            return build_connector(name, conn_cfgs[name])

        try:
            core_pipeline, connectors = build_pipeline(
                cfg, connectors=connectors, connector_factory=_factory
            )
        except ConfigError as e:
            raise _PipelineBuildError(f"pipeline build failed: {e}") from e
        ctx = Context(pipeline_name=core_pipeline.name, run_id=str(run_id))

        # Connect + run + close happen in a single worker thread so drivers
        # (notably sqlite3) bound to a thread don't trip on cross-thread reuse.
        return functools.partial(
            _run_pipeline_in_thread,
            core_pipeline,
            ctx,
            connectors,
            cursor_from=cursor_from,
            cursor_to=cursor_to,
        ), core_pipeline.name

    def _prepare_node_level(
        self,
        cfg: PipelineConfig,
        conn_cfgs: dict[str, ConnectionConfig],
        run_id: UUID,
    ) -> tuple[Callable[[], RunResult], str]:
        """Build the graph Task for per-node execution (ADR-0041 H2c).

        Uses ``connector_factory=None`` so ``sink.name`` stays a plain connection
        name (no dedicated-sink suffix) — the concurrent executor mints its own
        connector instance per node. Stub connectors satisfy ``build_pipeline``'s
        presence check; they're never connected. Stores task/conn_cfgs on ``self``
        and returns a sentinel runner (``execute`` branches on ``_node_level_active``
        and calls the async concurrent executor directly — never invokes this).
        """
        stub: dict[str, Connector] = {}
        for name, conn_cfg in conn_cfgs.items():
            try:
                stub[name] = build_connector(name, conn_cfg)
            except (ConfigError, RegistryError) as e:
                raise _PipelineBuildError(f"connection {name!r}: {type(e).__name__}: {e}") from e
        try:
            node_pipeline, _ = build_pipeline(cfg, connectors=stub, connector_factory=None)
        except ConfigError as e:
            raise _PipelineBuildError(f"pipeline build failed: {e}") from e
        task = node_pipeline.tasks[0]
        self._node_level_active = True
        self._node_level_task = task
        self._node_level_conn_cfgs = conn_cfgs
        self._node_level_run_id = str(run_id)
        self._node_deps = {n.id: [] for n in task.graph_nodes}
        for edge in task.graph_edges:
            self._node_deps[edge.to_id].append(edge.from_id)

        def _unused() -> RunResult:  # pragma: no cover - sentinel, execute() branches earlier
            raise RuntimeError(
                "node-level sentinel runner — execute() should branch on _node_level_active"
            )

        return _unused, node_pipeline.name

    async def _resolve_connection_configs(
        self, pipeline: Pipeline, cfg: PipelineConfig, session: AsyncSession
    ) -> dict[str, ConnectionConfig]:
        """Resolve every referenced connection to a secret-resolved ConnectionConfig."""
        names = referenced_connection_names(cfg)
        rows = await load_connections_by_name(
            session, workspace_id=pipeline.workspace_id, names=names
        )
        missing = [n for n in names if n not in rows]
        if missing:
            raise _PipelineBuildError(f"connection(s) not found in workspace: {sorted(missing)}")
        out: dict[str, ConnectionConfig] = {}
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
                out[name] = ConnectionConfig.model_validate({"type": row.type, **resolved})
            except ValidationError as e:
                raise _PipelineBuildError(
                    f"connection {name!r}: invalid config: {e.errors()}"
                ) from e
        return out


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


def _record_cancelled(run: Run) -> None:
    """Mark a run as user-cancelled (Phase P, 2026-05-28).

    Distinct from :func:`_record_failure` because no error occurred —
    the operator told us to stop. ``error_class`` / ``error_message``
    stay null so the UI doesn't show a misleading failure card on a
    voluntary stop. ``cancel_requested_at`` already carries the
    "when" (stamped by the REST endpoint); we only flip status +
    finished_at here.
    """
    now = datetime.now(UTC)
    run.status = RunStatus.CANCELLED
    run.finished_at = now
    run.heartbeat_at = now


def _run_pipeline_in_thread(
    pipeline: CorePipeline,
    context: Context,
    connectors: dict[str, Connector],
    *,
    cursor_from: Any = None,
    cursor_to: Any = None,
) -> RunResult:
    """Open connectors, run the pipeline, close connectors — all in one thread.

    Some drivers (sqlite3 in particular) refuse to be touched from a
    thread other than the one that opened them, so we can't split
    ``connect`` / ``run`` / ``close`` across multiple ``asyncio.to_thread``
    calls — each call may land on a different pool worker.

    ``cursor_from`` / ``cursor_to`` (when set) run a backfill over the task's
    ``cursor_column`` instead of a full read (ADR-0039).
    """
    for c in connectors.values():
        c.connect()
    try:
        return pipeline.run(
            context, connectors=connectors, cursor_from=cursor_from, cursor_to=cursor_to
        )
    finally:
        for c in connectors.values():
            with contextlib.suppress(Exception):
                c.close()


__all__ = ["RunExecutor"]
