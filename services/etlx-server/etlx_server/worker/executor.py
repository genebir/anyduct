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
from etl_plugins.core.column_lineage import ColumnLineage
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
                        #
                        # Phase MM (ADR-0057, 2026-05-29): resolve workspace
                        # vars *before* deriving lineage so asset keys reflect
                        # the resolved table names the pipeline actually
                        # wrote to.
                        lineage, resolved_cfg = await self._lineage_for_resolved(
                            session, run, version, log
                        )
                        if lineage is not None and resolved_cfg is not None:
                            await self._persist_lineage(session, run, result, lineage, log)
                            await self._persist_column_lineage(
                                session, run, version, lineage, log, resolved_cfg=resolved_cfg
                            )
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
        parse/derive error (best-effort — lineage never fails a run).

        Note: this overload uses the raw ``config_json`` without resolving
        workspace variables. Callers that need resolved asset keys (e.g.
        when the pipeline references ``${var.target_table}``) should use
        :meth:`_lineage_for_resolved` instead, which is the path the
        success branch in :meth:`execute` takes.
        """
        from etl_plugins.runtime.lineage import derive_lineage

        try:
            return derive_lineage(PipelineConfig.model_validate(version.config_json))
        except Exception as e:
            log.warning("run.lineage_derive_failed", error_class=type(e).__name__, error=str(e))
            return None

    async def _lineage_for_resolved(
        self,
        session: AsyncSession,
        run: Run,
        version: PipelineVersion,
        log: Any,
    ) -> tuple[AssetLineage | None, PipelineConfig | None]:
        """Resolve workspace vars + derive lineage from the resolved config.

        Returns ``(lineage, cfg)`` so the caller can reuse the resolved
        config for column-lineage emission (which would otherwise
        re-resolve and risk drift). Phase MM (ADR-0057, 2026-05-29):
        without this resolution step the catalog asset keys retained
        unresolved ``${var.name}`` placeholders even though the pipeline
        wrote to the *resolved* table — the catalog disagreed with
        reality, a silent-correctness bug.
        """
        from etl_plugins.runtime.lineage import derive_lineage

        try:
            global_vars = await WorkspaceVariableRepository(session).as_dict(
                workspace_id=run.workspace_id
            )
            cfg_dict = resolve_config_variables(version.config_json, extra=global_vars)
            cfg = PipelineConfig.model_validate(cfg_dict)
        except Exception as e:
            log.warning(
                "run.lineage_derive_failed",
                error_class=type(e).__name__,
                error=str(e),
                phase="resolve_or_validate",
            )
            return None, None
        try:
            return derive_lineage(cfg), cfg
        except Exception as e:
            log.warning(
                "run.lineage_derive_failed",
                error_class=type(e).__name__,
                error=str(e),
                phase="derive",
            )
            return None, cfg

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
        *,
        resolved_cfg: PipelineConfig | None = None,
    ) -> None:
        """Record per-column lineage for this run's output assets (ADR-0041 J2).

        Best-effort: a column-lineage glitch (parse error, unknown transform,
        etc.) never flips a successful run to failed. Runs *after*
        :meth:`_persist_lineage` so the asset rows exist for the repo to
        reference by key.

        Phase Z (ADR-0045, 2026-05-28): when a source query contains
        ``SELECT *``, we first fetch the table schema via the connector's
        :class:`SchemaInspector` capability and pass it to
        :func:`derive_column_lineage`. That lets the lineage walker expand
        the star projection into real column edges instead of marking the
        sink opaque — closing the last "I just don't know" path in the
        lineage axis.

        Phase MM (ADR-0057, 2026-05-29): callers in the success path pass
        ``resolved_cfg`` so column-asset keys match the asset-axis
        lineage (which already resolved workspace variables). Without
        this, column rows would be attached to the *unresolved*
        ``${var.X}`` key and not find their asset row.
        """
        from etl_plugins.runtime.column_lineage import derive_column_lineage
        from etlx_server.assets.repository import AssetRepository

        if not lineage.outputs:
            return
        if resolved_cfg is not None:
            cfg = resolved_cfg
        else:
            try:
                cfg = PipelineConfig.model_validate(version.config_json)
            except Exception as e:  # parse or unsupported shape — fall back silently
                log.warning(
                    "run.column_lineage_derive_failed",
                    error_class=type(e).__name__,
                    error=str(e),
                )
                return
        schemas = await self._build_schemas_for_star_queries(session, run, version, log)
        try:
            col_lineage = derive_column_lineage(cfg, schemas=schemas)
        except Exception as e:
            log.warning(
                "run.column_lineage_derive_failed",
                error_class=type(e).__name__,
                error=str(e),
            )
            return

        # Phase AA (ADR-0046, 2026-05-29): schema-passthrough fallback.
        # When a sink came out opaque (a python/custom_python/sql_exec
        # transform blocked the static derivation), try to recover a
        # 1:1 lineage by intersecting the source and sink schemas. Any
        # column whose name appears on both sides is treated as a
        # passthrough — the dominant pattern for "tweak some values but
        # keep the row shape" transforms.
        if col_lineage.opaque_assets:
            try:
                col_lineage = await self._augment_opaque_with_schema_passthrough(
                    session=session,
                    run=run,
                    cfg=cfg,
                    col_lineage=col_lineage,
                    seed_schemas=schemas,
                    log=log,
                )
            except Exception as e:  # never fail the run on a lineage glitch
                log.warning(
                    "run.column_lineage_passthrough_failed",
                    error_class=type(e).__name__,
                    error=str(e),
                )

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

    async def _build_schemas_for_star_queries(
        self,
        session: AsyncSession,
        run: Run,
        version: PipelineVersion,
        log: Any,
    ) -> dict[str, dict[str, dict[str, str]]]:
        """Fetch the schema dict only for tables referenced by ``SELECT *``.

        Returns ``{connection_name: {table_name: {column_name: type}}}``
        suitable for :func:`derive_column_lineage(..., schemas=...)`.

        The scan is cheap (string ``"*" in query`` then sqlglot AST walk
        for table names) so we run it unconditionally; only sources whose
        query actually contains ``*`` trigger a connection inspection.
        Connections whose connector doesn't implement
        :class:`SchemaInspector` (HTTP, Kafka, …) are silently skipped —
        their queries can't be ``SELECT *`` in any meaningful sense.
        """
        from collections import defaultdict

        from etl_plugins.runtime.sql_lineage import extract_referenced_tables
        from etlx_server.connections.inspect import (
            ConnectionInspector,
            InspectionUnsupportedError,
        )

        cfg_data = version.config_json or {}
        needs: dict[str, set[str]] = defaultdict(set)

        def _visit(source: dict[str, Any] | None) -> None:
            if not isinstance(source, dict):
                return
            query = source.get("query")
            connection = source.get("connection")
            if not isinstance(query, str) or not isinstance(connection, str):
                return
            if "*" not in query:
                return
            for tbl in extract_referenced_tables(query):
                needs[connection].add(tbl)

        # Top-level (single-task) source.
        _visit(cfg_data.get("source"))
        # Task-DAG sources.
        for t in cfg_data.get("tasks", []) or []:
            if isinstance(t, dict):
                _visit(t.get("source"))
        # Graph source nodes.
        graph = cfg_data.get("graph")
        if isinstance(graph, dict):
            for node in graph.get("nodes", []) or []:
                if isinstance(node, dict) and node.get("type") == "source":
                    _visit(node)

        if not needs:
            return {}

        try:
            rows = await load_connections_by_name(
                session, workspace_id=run.workspace_id, names=list(needs)
            )
        except Exception as e:
            log.warning("run.column_lineage_schema_lookup_failed", error=str(e))
            return {}

        inspector = ConnectionInspector(self._backend)
        schemas: dict[str, dict[str, dict[str, str]]] = {}
        for name, conn in rows.items():
            sub: dict[str, dict[str, str]] = {}
            for tbl in needs[name]:
                try:
                    cols = await inspector.list_columns(conn, tbl)
                except InspectionUnsupportedError:
                    # Connector type can't introspect — give up on this
                    # whole connection silently.
                    sub = {}
                    break
                except Exception as e:
                    # Per-table failure (table missing, perms): skip just this
                    # table but keep trying the others.
                    log.warning(
                        "run.column_lineage_schema_table_failed",
                        connection=name,
                        table=tbl,
                        error=str(e),
                    )
                    continue
                sub[tbl] = {c.name: c.type for c in cols}
            if sub:
                schemas[name] = sub
        return schemas

    async def _augment_opaque_with_schema_passthrough(
        self,
        *,
        session: AsyncSession,
        run: Run,
        cfg: PipelineConfig,
        col_lineage: ColumnLineage,
        seed_schemas: dict[str, dict[str, dict[str, str]]],
        log: Any,
    ) -> ColumnLineage:
        """Schema-passthrough fallback for opaque sinks (ADR-0046).

        When :func:`derive_column_lineage` couldn't trace a sink's columns —
        because some transform in the chain (``python`` / ``custom_python`` /
        ``sql_exec`` / unknown) is opaque to static analysis — we still want
        the catalog to show *something*. The conservative guess is **column
        name passthrough**: any column whose name appears in both the
        source's and the sink's schema is treated as a 1:1 attribution.

        Why this is defensible:

        * The dominant python-transform pattern is "tweak values, keep the
          schema" — adding a derived flag, filtering rows, normalising text.
          A 1:1 name match matches what *actually happens* in those cases.
        * Columns that only exist on one side (the python code added them,
          or dropped them) stay un-attributed — no fabricated upstream.
        * Asset-level lineage (source → sink edges) is already auto-derived
          by :func:`derive_lineage`; this only adds the missing column rows.

        Returns the updated :class:`ColumnLineage` with passthrough edges
        appended and any sink we managed to enrich removed from
        ``opaque_assets``. Sinks whose schema we can't fetch (HTTP / Kafka
        sinks, permissions issues) stay opaque — that's the truthful state.
        """
        from collections import defaultdict

        from etl_plugins.core.asset import AssetKey
        from etl_plugins.core.column_lineage import ColumnEdge, ColumnLineage, ColumnRef
        from etl_plugins.runtime.lineage import derive_lineage
        from etlx_server.connections.inspect import (
            ConnectionInspector,
            InspectionUnsupportedError,
        )

        # 1. Use the asset axis to learn which source asset(s) feed each opaque sink.
        asset_lineage = derive_lineage(cfg)
        sink_to_sources: dict[AssetKey, list[AssetKey]] = defaultdict(list)
        for edge in asset_lineage.edges:
            sink_to_sources[edge.downstream].append(edge.upstream)

        opaque_set = {str(k): k for k in col_lineage.opaque_assets}
        if not opaque_set:
            return col_lineage

        # 2. Figure out which connection/table schemas we still need
        # to fetch (anything not already in seed_schemas).
        needed: dict[str, set[str]] = defaultdict(set)

        def _need(key: AssetKey) -> None:
            if len(key.path) < 2:
                return
            connection_name, table_name = key.path[0], key.path[1]
            already = seed_schemas.get(connection_name, {}).get(table_name)
            if already is None:
                needed[connection_name].add(table_name)

        for sink_key in opaque_set.values():
            _need(sink_key)
            for src_key in sink_to_sources.get(sink_key, []):
                _need(src_key)

        # 3. Fetch what we don't have yet.
        fetched: dict[str, dict[str, dict[str, str]]] = {}
        if needed:
            try:
                conn_rows = await load_connections_by_name(
                    session, workspace_id=run.workspace_id, names=list(needed)
                )
            except Exception as e:
                log.warning("run.column_lineage_passthrough_conn_lookup_failed", error=str(e))
                conn_rows = {}

            inspector = ConnectionInspector(self._backend)
            for conn_name, conn_row in conn_rows.items():
                sub: dict[str, dict[str, str]] = {}
                for tbl in needed[conn_name]:
                    try:
                        cols = await inspector.list_columns(conn_row, tbl)
                    except InspectionUnsupportedError:
                        # Connector type doesn't introspect schemas. Bail
                        # on this whole connection — likely an HTTP/Kafka
                        # sink we genuinely can't infer.
                        sub = {}
                        break
                    except Exception as e:
                        log.warning(
                            "run.column_lineage_passthrough_table_failed",
                            connection=conn_name,
                            table=tbl,
                            error=str(e),
                        )
                        continue
                    sub[tbl] = {c.name: c.type for c in cols}
                if sub:
                    fetched[conn_name] = sub

        # 4. Build a merged schema map: seed (SELECT-* schemas) + freshly fetched.
        full: dict[str, dict[str, dict[str, str]]] = {}
        for source in (seed_schemas, fetched):
            for conn_name, tables in source.items():
                full.setdefault(conn_name, {}).update(tables)

        def _columns(key: AssetKey) -> set[str]:
            if len(key.path) < 2:
                return set()
            return set(full.get(key.path[0], {}).get(key.path[1], {}))

        # 5. Walk each opaque sink and emit passthrough edges where we can.
        #
        # Two-step emission per sink:
        #   (a) for every shared column name → emit a ``ColumnEdge`` with
        #       the matching source ``ColumnRef``(s) as upstream.
        #   (b) for every sink-only column (e.g. one the python code added)
        #       → emit a ``ColumnEdge`` with an *empty* upstream tuple.
        #       This makes the column visible in the catalog (matching the
        #       physical sink schema) rather than silently dropping it.
        #
        # The sink is only kept opaque when we can't fetch its schema at
        # all — then we genuinely don't know what columns exist.
        new_edges = list(col_lineage.edges)
        still_opaque: list[AssetKey] = []
        for sink_key in opaque_set.values():
            sink_cols = _columns(sink_key)
            if not sink_cols:
                still_opaque.append(sink_key)
                continue
            per_col_upstreams: dict[str, list[ColumnRef]] = defaultdict(list)
            for src_key in sink_to_sources.get(sink_key, []):
                shared = sink_cols & _columns(src_key)
                for col in shared:
                    per_col_upstreams[col].append(ColumnRef(src_key, col))
            for col in sink_cols:
                upstreams = tuple(per_col_upstreams.get(col, []))
                new_edges.append(
                    ColumnEdge(
                        downstream=ColumnRef(sink_key, col),
                        upstreams=upstreams,
                    )
                )

        return ColumnLineage(edges=new_edges, opaque_assets=still_opaque)

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

        # Per-node records_read snapshot for graph node-level runs.
        # Used to attach a volume number to the source-read audit rows
        # (Phase W, 2026-05-28). Empty dict for non-node-level — the
        # source-read audit still fires, just without a per-node count.
        records_read_by_node: dict[str, int] = (
            {o.node_id: o.records_read for o in self._node_outcomes}
            if self._node_outcomes is not None
            else {}
        )

        # Phase W (2026-05-28): user wants SELECT queries audited too
        # ("각 쿼리"), not only data-mutating sql_exec / python paths
        # Phase U recorded. Compliance need: "who read PII?" matters
        # for GDPR / SOX. Restrict to SQL connection types so
        # HTTP/Kafka/S3 sources whose ``query`` field means something
        # different (path / topic / prefix) don't get mislabelled.
        # Future slices can add run.http_read / run.kafka_read.
        # Phase AAQ (2026-05-29) adds Vertica + MSSQL — both RDBMS,
        # both implement the SQL audit contract via the same
        # query-string semantics.
        _SQL_CONNECTION_TYPES = {  # noqa: N806 — const-style local for clarity
            "postgres",
            "mysql",
            "sqlite",
            "vertica",
            "mssql",
            "snowflake",
            "bigquery",
            "redshift",
            "clickhouse",
            "cassandra",
        }
        connection_type_by_name: dict[str, str] = {}
        try:
            conn_names = referenced_connection_names(cfg)
            if conn_names:
                rows = await load_connections_by_name(
                    session, workspace_id=run.workspace_id, names=conn_names
                )
                connection_type_by_name = {n: r.type for n, r in rows.items()}
        except Exception as e:  # best-effort — skip source-read auditing if lookup fails
            log.warning("run.audit_conn_type_lookup_failed", error=str(e))

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

        async def _record_sql_read(*, node_id: str, connection: str | None, query: str) -> None:
            """Phase W (2026-05-28): audit a source-side SELECT against a
            SQL connection. Skipped when the connection type isn't one
            of the SQL ones — see the comment on _SQL_CONNECTION_TYPES
            above for why we don't mislabel HTTP/Kafka/S3 reads as
            SQL."""
            if not connection or not query:
                return
            ctype = connection_type_by_name.get(connection)
            if ctype not in _SQL_CONNECTION_TYPES:
                return
            after: dict[str, Any] = {
                "node_id": node_id,
                "kind": "source",
                "connection": connection,
                "connection_type": ctype,
                "query": query[:2000],
                "query_truncated": len(query) > 2000,
                "query_hash": _hash(query),
            }
            # Per-node records_read is only known in node-level runs;
            # attach when available so forensics can answer "how many
            # rows were exposed?". For non-node-level the total run
            # records_read already lives on the run row.
            if node_id in records_read_by_node:
                after["records_read"] = records_read_by_node[node_id]
            await audit.record(
                actor_user_id=run.triggered_by_user_id,
                workspace_id=run.workspace_id,
                action="run.sql_read",
                resource_type="run",
                resource_id=str(run.id),
                after=after,
            )

        # ── graph shape (ADR-0030 + ADR-0042) ──────────────────────
        if cfg.graph is not None:
            for node in cfg.graph.nodes:
                if executed_node_ids is not None and node.id not in executed_node_ids:
                    continue
                if node.type == "source" and node.query:
                    # Phase W: SQL source read. ``_record_sql_read``
                    # filters out non-SQL connection types itself, so a
                    # truthy ``query`` on an HTTP source (where the
                    # field means "path") doesn't generate a misleading
                    # run.sql_read row.
                    await _record_sql_read(
                        node_id=node.id,
                        connection=node.connection,
                        query=node.query,
                    )
                elif node.type == "sql_exec":
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
            # Phase W: source SELECT in the linear shape. ``query`` is
            # optional (some sources read by table name only), so
            # check truthy. Same SQL-connection-type filter as the
            # graph path applies.
            src_q = task.source.query
            if src_q:
                await _record_sql_read(
                    node_id=task.name,
                    connection=task.source.connection,
                    query=src_q,
                )
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
