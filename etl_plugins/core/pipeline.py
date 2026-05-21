"""Pipeline + Task: the in-Python orchestration API. SPEC.md §4.4.

Batch execution is via the sync :meth:`Pipeline.run`. Stream execution is via
the async :meth:`Pipeline.arun_stream` (Step 3.2).
Retry, DLQ routing, and auto-metrics emit are wired in Step 3.3.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from types import CodeType
from typing import Any

from etl_plugins.config.models import DlqConfig, RetryConfig
from etl_plugins.core.asset import (
    AssetKey,
    AssetLineage,
    LineageEdge,
    asset_kind,
    derive_asset_key,
)
from etl_plugins.core.connector import (
    BatchSink,
    BatchSource,
    Connector,
    StreamSink,
    StreamSource,
)
from etl_plugins.core.context import Context
from etl_plugins.core.cursor import CursorValue
from etl_plugins.core.exceptions import PipelineError, TaskError, TransformError
from etl_plugins.core.record import Record
from etl_plugins.core.sql_exec import SqlExecutor
from etl_plugins.observability.lineage import (
    COMPLETE,
    FAIL,
    START,
    LineageEvent,
    get_lineage_emitter,
)
from etl_plugins.observability.metrics import (
    DURATION_SECONDS,
    ERRORS_TOTAL,
    RECORDS_READ_TOTAL,
    RECORDS_WRITTEN_TOTAL,
    get_metrics,
)
from etl_plugins.observability.tracing import get_tracer
from etl_plugins.utils.retry import retryable

TransformFn = Callable[[Record], Record | None]
"""Transform: takes a Record, returns the (possibly modified) Record, or None to drop it."""

# Task-orchestration DAG task states (ADR-0028).
TASK_SUCCESS = "success"
TASK_FAILED = "failed"
TASK_SKIPPED = "skipped"
TASK_UPSTREAM_FAILED = "upstream_failed"

# Per-task trigger rules — when a task runs given its upstream states.
TRIGGER_RULES = frozenset({"all_success", "all_done", "one_success", "none_failed"})
DEFAULT_TRIGGER_RULE = "all_success"

Hook = Callable[..., None]
"""Pipeline hook — receives positional args specific to the event."""


@dataclass
class SinkSpec:
    """One fan-out sink target (ADR-0026).

    ``Task.sinks`` holds these for one-source → many-sink pipelines. The legacy
    flat ``Task.sink*`` fields model the single-sink case and stay the default.
    """

    name: str
    table: str | None = None
    mode: str = "append"
    key_columns: list[str] | None = None
    options: dict[str, Any] = field(default_factory=dict)
    # Conditional routing predicate (ADR-0027). Sandboxed Python expression
    # evaluated per (transformed) record; ``None`` means this is a default sink.
    when: str | None = None
    # Atomic pre-write SQL (ADR-0035). Runs as the first statement *inside* this
    # sink's write transaction (before TRUNCATE/COPY/upsert), so a DELETE + the
    # insert commit together — atomic delete-then-insert idempotency. RDBMS
    # sinks only; runs even on empty input (clears the partition).
    pre_sql: str | None = None


@dataclass(frozen=True)
class SqlAction:
    """A SQL statement run once, before the load, against ``connection``
    (ADR-0035). Used for delete-then-insert idempotency — e.g. clearing the
    target rows this run will re-insert. The connection must resolve to a
    connector implementing :class:`~etl_plugins.core.sql_exec.SqlExecutor`."""

    connection: str
    statement: str


@dataclass
class GraphNode:
    """One node in a dataflow graph (ADR-0030, join added in ADR-0041)."""

    id: str
    kind: str  # source | transform | sink | join
    # source
    source_name: str | None = None
    query: str | None = None
    source_options: dict[str, Any] = field(default_factory=dict)
    # transform
    transform_fn: TransformFn | None = None
    # sink
    sink: SinkSpec | None = None
    # join (fan-in, ADR-0041) — merge ≥2 inputs on ``join_on`` keys
    join_on: list[str] | None = None
    join_how: str = "inner"


@dataclass
class GraphEdge:
    """A directed edge ``from_id → to_id`` with an optional ``when`` predicate."""

    from_id: str
    to_id: str
    when: str | None = None


@dataclass
class BranchRule:
    """One branch rule (ADR-0028, BranchPythonOperator analog).

    After a branch task runs, rules are evaluated in order against the task's
    outcome (``records_read`` / ``records_written`` / ``success`` in scope, no
    builtins). The first rule whose ``when`` is truthy selects ``to`` (direct
    downstream task names); a ``when`` of ``None`` is the default/else. Direct
    downstream tasks not selected are skipped, and the skip propagates via
    trigger rules.
    """

    when: str | None
    to: list[str] = field(default_factory=list)


@dataclass
class Task:
    """One ETL task: extract → transform* → load."""

    name: str | None = None
    source: str | None = None
    query: str | None = None
    source_options: dict[str, Any] = field(default_factory=dict)
    # Cursor column for incremental reads (Step 6.1). When set, ``Pipeline.run``
    # with ``cursor_from`` / ``cursor_to`` routes through ``source.read_since``;
    # the field must exist on every emitted record's ``data``.
    cursor_column: str | None = None
    transforms: list[TransformFn] = field(default_factory=list)
    # SQL statements run once before the load (ADR-0035) — e.g. a DELETE to make
    # an append re-runnable (delete-then-insert). Run in order, before reading.
    pre_sql: list[SqlAction] = field(default_factory=list)
    sink: str | None = None
    sink_table: str | None = None
    sink_mode: str = "append"
    sink_key_columns: list[str] | None = None
    sink_options: dict[str, Any] = field(default_factory=dict)
    sink_pre_sql: str | None = None
    # Fan-out targets (ADR-0026). When non-empty these take precedence over the
    # flat ``sink*`` fields and the source is re-read once per sink.
    sinks: list[SinkSpec] = field(default_factory=list)
    # Task-orchestration DAG (ADR-0028). Names of tasks that must complete
    # before this one runs. Empty ⇒ a root task (no upstream). When any task in
    # a pipeline declares ``depends_on``, every task must have a unique ``name``.
    depends_on: list[str] = field(default_factory=list)
    # When this task runs given its upstream states. See ``TRIGGER_RULES``.
    trigger_rule: str = DEFAULT_TRIGGER_RULE
    # Branch selection rules (ADR-0028). Non-empty ⇒ this is a branch task that
    # chooses which direct downstream tasks run; the rest are skipped.
    branch: list[BranchRule] = field(default_factory=list)
    # Dataflow graph (ADR-0030). When ``graph_nodes`` is non-empty the task runs
    # as an operator graph (records flow along edges, branching per-edge ``when``)
    # instead of the flat source→transforms→sinks path.
    graph_nodes: list[GraphNode] = field(default_factory=list)
    graph_edges: list[GraphEdge] = field(default_factory=list)

    def effective_sinks(self) -> list[SinkSpec]:
        """Normalised sink list — the flat ``sink`` becomes a one-element list."""
        if self.sinks:
            return self.sinks
        if self.sink is None:
            return []
        return [
            SinkSpec(
                name=self.sink,
                table=self.sink_table,
                mode=self.sink_mode,
                key_columns=self.sink_key_columns,
                options=self.sink_options,
                pre_sql=self.sink_pre_sql,
            )
        ]

    @classmethod
    def extract(
        cls,
        source: str,
        query: str | None = None,
        *,
        name: str | None = None,
        cursor_column: str | None = None,
        **options: Any,
    ) -> Task:
        return cls(
            name=name,
            source=source,
            query=query,
            source_options=dict(options),
            cursor_column=cursor_column,
        )

    def transform(self, fn: TransformFn) -> Task:
        self.transforms.append(fn)
        return self

    def load(
        self,
        sink: str,
        *,
        table: str | None = None,
        mode: str = "append",
        key_columns: list[str] | None = None,
        **options: Any,
    ) -> Task:
        self.sink = sink
        self.sink_table = table
        self.sink_mode = mode
        self.sink_key_columns = key_columns
        self.sink_options = dict(options)
        return self


@dataclass
class RunResult:
    """Outcome of a single Pipeline.run."""

    run_id: str
    pipeline_name: str
    success: bool
    records_read: int = 0
    records_written: int = 0
    duration_seconds: float = 0.0
    error: BaseException | None = None
    # Max cursor value seen across all tasks during a cursored run (Step 6.1).
    # ``None`` either means the pipeline wasn't cursored or no records were
    # emitted. Callers persist this back into their CursorState for the next
    # resume.
    new_cursor: CursorValue = None
    # Per-task terminal state (ADR-0028). Empty for non-DAG runs; otherwise maps
    # task name → success / failed / skipped / upstream_failed.
    task_states: dict[str, str] = field(default_factory=dict)


def _toposort_nodes(by_id: dict[str, GraphNode], edges: list[GraphEdge]) -> list[str]:
    """Kahn topological order of node ids; raise on a cycle (ADR-0041)."""
    indeg = dict.fromkeys(by_id, 0)
    downstream: dict[str, list[str]] = {nid: [] for nid in by_id}
    for e in edges:
        indeg[e.to_id] += 1
        downstream[e.from_id].append(e.to_id)
    ready = [nid for nid, d in indeg.items() if d == 0]
    order: list[str] = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for nxt in downstream[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
    if len(order) != len(by_id):
        raise TaskError("graph has a cycle")
    return order


def _join_records(left: list[Record], right: list[Record], on: list[str], how: str) -> list[Record]:
    """Hash-join two record lists on ``on`` keys (ADR-0041).

    ``how`` is inner | left | right | outer. Right columns are merged onto a
    copy of the left record (right wins on non-key conflicts); unmatched rows
    are emitted alone for the outer side(s).
    """
    if not on:
        raise TaskError("join requires non-empty 'on' key columns")

    def key_of(rec: Record, side: str) -> tuple[Any, ...]:
        try:
            return tuple(rec.data[k] for k in on)
        except KeyError as exc:
            raise TransformError(f"join key {exc} missing in {side} input") from exc

    index: dict[tuple[Any, ...], list[Record]] = {}
    for r in right:
        index.setdefault(key_of(r, "right"), []).append(r)

    out: list[Record] = []
    matched: set[tuple[Any, ...]] = set()
    for lrec in left:
        k = key_of(lrec, "left")
        rights = index.get(k)
        if rights:
            matched.add(k)
            for r in rights:
                out.append(
                    Record(
                        data={**lrec.data, **r.data},
                        metadata=lrec.metadata,
                        schema_version=lrec.schema_version,
                    )
                )
        elif how in ("left", "outer"):
            out.append(lrec)
    if how in ("right", "outer"):
        for k, rights in index.items():
            if k not in matched:
                out.extend(rights)
    return out


def _hash_join(inputs: list[list[Record]], on: list[str], how: str) -> list[Record]:
    """Fold ≥2 inputs left-to-right with :func:`_join_records`."""
    result = inputs[0]
    for nxt in inputs[1:]:
        result = _join_records(result, nxt, on, how)
    return result


@dataclass
class Pipeline:
    """A named sequence of Tasks.

    The caller is responsible for opening/closing connector instances passed to
    ``run``. Configuration-driven instantiation arrives in Step 1.5.
    """

    name: str
    mode: str = "batch"  # batch | stream
    tasks: list[Task] = field(default_factory=list)
    commit_strategy: str = "after_sink_flush"  # used by stream runtime; see SPEC.md §5.5
    retry: RetryConfig | None = None  # if set, wrap each task with @retryable
    dlq: DlqConfig | None = None  # if set, route TransformError records to this sink
    _hooks: dict[str, list[Hook]] = field(default_factory=dict)

    def add(self, task: Task) -> Pipeline:
        self.tasks.append(task)
        return self

    def on(self, event: str, hook: Hook) -> Pipeline:
        """Register a hook. Events: pre_run, post_run, on_error, on_task_start, on_task_end."""
        self._hooks.setdefault(event, []).append(hook)
        return self

    def _ordered_tasks(self) -> list[Task]:
        """Tasks in dependency (topological) order — Task-orchestration DAG (ADR-0028).

        Backward compatible: when no task declares ``depends_on``, the original
        list order is returned unchanged. Otherwise a stable Kahn's-algorithm
        sort runs (ties broken by original list position, so a partially-ordered
        DAG still reads predictably). Raises :class:`PipelineError` on a missing
        dependency reference, a duplicate/blank task name, or a cycle.
        """
        if not any(t.depends_on for t in self.tasks):
            return self.tasks

        by_name: dict[str, Task] = {}
        order: dict[str, int] = {}  # original index, for stable tie-breaking
        for i, t in enumerate(self.tasks):
            if not t.name:
                raise PipelineError(
                    "every task needs a non-empty 'name' when 'depends_on' is used "
                    f"(offending task: {t!r})"
                )
            if t.name in by_name:
                raise PipelineError(f"duplicate task name in DAG: {t.name!r}")
            by_name[t.name] = t
            order[t.name] = i

        indegree: dict[str, int] = dict.fromkeys(by_name, 0)
        dependents: dict[str, list[str]] = {name: [] for name in by_name}
        for name, t in by_name.items():
            for dep in t.depends_on:
                if dep not in by_name:
                    raise PipelineError(f"task {name!r} depends on unknown task {dep!r}")
                if dep == name:
                    raise PipelineError(f"task {name!r} depends on itself")
                indegree[name] += 1
                dependents[dep].append(name)

        ready = sorted((n for n, d in indegree.items() if d == 0), key=lambda n: order[n])
        ordered_names: list[str] = []
        while ready:
            name = ready.pop(0)
            ordered_names.append(name)
            newly_ready: list[str] = []
            for child in dependents[name]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    newly_ready.append(child)
            if newly_ready:
                ready = sorted([*ready, *newly_ready], key=lambda n: order[n])

        if len(ordered_names) != len(by_name):
            remaining = sorted(set(by_name) - set(ordered_names))
            raise PipelineError(f"dependency cycle detected among tasks: {remaining}")
        return [by_name[n] for n in ordered_names]

    def lineage(self) -> AssetLineage:
        """Derived-first lineage of this pipeline (ADR-0036): the input assets
        (sources) and output assets (sinks), with ``input → output`` edges.
        Connection-split sink keys (``name::sink``, ADR-0034) are normalised
        back to the original connection so lineage isn't fragmented."""
        inputs: list[AssetKey] = []
        outputs: list[AssetKey] = []
        edges: list[LineageEdge] = []
        kinds: dict[AssetKey, str | None] = {}
        seen_in: set[AssetKey] = set()
        seen_out: set[AssetKey] = set()
        seen_edge: set[tuple[AssetKey, AssetKey]] = set()

        def record_kind(k: AssetKey, fields: dict[str, Any]) -> None:
            kind = asset_kind(fields)
            if kind and not kinds.get(k):
                kinds[k] = kind
            kinds.setdefault(k, kind)

        def add_in(k: AssetKey | None, fields: dict[str, Any]) -> None:
            if k is None:
                return
            record_kind(k, fields)
            if k not in seen_in:
                seen_in.add(k)
                inputs.append(k)

        def add_out(k: AssetKey | None, fields: dict[str, Any]) -> None:
            if k is None:
                return
            record_kind(k, fields)
            if k not in seen_out:
                seen_out.add(k)
                outputs.append(k)

        def add_edge(u: AssetKey | None, d: AssetKey | None) -> None:
            if u is not None and d is not None and (u, d) not in seen_edge:
                seen_edge.add((u, d))
                edges.append(LineageEdge(upstream=u, downstream=d))

        def sink_fields(spec: SinkSpec) -> dict[str, Any]:
            return {"table": spec.table, **spec.options}

        def sink_key(spec: SinkSpec) -> AssetKey | None:
            conn = spec.name.removesuffix("::sink") if spec.name else spec.name
            return derive_asset_key(conn, sink_fields(spec))

        for task in self.tasks:
            if task.graph_nodes:
                src_keys: list[AssetKey] = []
                for n in task.graph_nodes:
                    if n.kind == "source":
                        sf = {"query": n.query, **n.source_options}
                        k = derive_asset_key(n.source_name, sf)
                        add_in(k, sf)
                        if k is not None:
                            src_keys.append(k)
                    elif n.kind == "sink" and n.sink is not None:
                        k = sink_key(n.sink)
                        add_out(k, sink_fields(n.sink))
                        for sk in src_keys:
                            add_edge(sk, k)
                continue
            src_fields = {"query": task.query, **task.source_options}
            in_key = derive_asset_key(task.source, src_fields)
            add_in(in_key, src_fields)
            for spec in task.effective_sinks():
                out_key = sink_key(spec)
                add_out(out_key, sink_fields(spec))
                add_edge(in_key, out_key)

        return AssetLineage(inputs=inputs, outputs=outputs, edges=edges, kinds=kinds)

    def run(
        self,
        context: Context | None = None,
        *,
        connectors: dict[str, Connector] | None = None,
        cursor_from: CursorValue = None,
        cursor_to: CursorValue = None,
    ) -> RunResult:
        """Run the batch pipeline.

        Step 6.1 backfill parameters:

        * ``cursor_from`` — exclusive lower bound on each task's
          ``cursor_column`` (only records with value ``> cursor_from``).
          ``None`` means "no lower bound" (full backfill or first run).
        * ``cursor_to`` — inclusive upper bound (records with value
          ``<= cursor_to``). ``None`` means "no upper bound" (tail).
        * If either is set, every task must have ``cursor_column`` defined;
          a ``TaskError`` is raised otherwise. The source connector must
          implement :meth:`BatchSource.read_since`.

        ``RunResult.new_cursor`` carries the max cursor value seen across
        all tasks — persist it via ``CursorState`` for the next resume.
        """
        if self.mode != "batch":
            raise PipelineError(
                f"Pipeline.run is batch-only — for mode={self.mode!r} use arun_stream()"
            )

        cursored = cursor_from is not None or cursor_to is not None

        ctx = context or Context(pipeline_name=self.name)
        conns = connectors or {}
        start = time.monotonic()
        metrics = get_metrics()
        tracer = get_tracer()
        attrs = {"pipeline": self.name, "mode": self.mode}

        result = RunResult(run_id=ctx.run_id, pipeline_name=self.name, success=False)
        task_runner = self._run_task
        if self.retry is not None:
            task_runner = retryable(**self._retry_kwargs())(task_runner)

        # Lineage (ADR-0036): emit START now; COMPLETE/FAIL after the run. The
        # default emitter is a no-op, so this is free unless a backend is set.
        emitter = get_lineage_emitter()
        lin = self.lineage()
        lin_inputs = tuple(lin.inputs)
        lin_outputs = tuple(lin.outputs)
        emitter.emit(
            LineageEvent(
                event_type=START,
                run_id=ctx.run_id,
                job_name=self.name,
                inputs=lin_inputs,
                outputs=lin_outputs,
            )
        )

        # ``pipeline.run`` span wraps the whole sequence of tasks plus the
        # bookkeeping that emits aggregate counters. Task-level spans are
        # opened inside _run_dispatch below so they're nested children.
        run_span = tracer.start_span(
            "pipeline.run",
            attributes={
                "pipeline": self.name,
                "mode": self.mode,
                "run_id": ctx.run_id,
                **({"cursor_from": str(cursor_from)} if cursor_from is not None else {}),
                **({"cursor_to": str(cursor_to)} if cursor_to is not None else {}),
            },
        )
        self._fire("pre_run", ctx)
        try:
            ordered = self._ordered_tasks()
            if self._is_dag():
                self._run_dag(
                    ordered,
                    conns,
                    cursor_from,
                    cursor_to,
                    cursored=cursored,
                    ctx=ctx,
                    result=result,
                    metrics=metrics,
                    tracer=tracer,
                    attrs=attrs,
                    task_runner=task_runner,
                )
            else:
                for task in ordered:
                    self._execute_task(
                        task,
                        conns,
                        cursor_from,
                        cursor_to,
                        cursored=cursored,
                        ctx=ctx,
                        result=result,
                        metrics=metrics,
                        tracer=tracer,
                        attrs=attrs,
                        task_runner=task_runner,
                    )
            result.success = True
            run_span.set_attribute("success", True)
            run_span.set_attribute("records_read_total", result.records_read)
            run_span.set_attribute("records_written_total", result.records_written)
            emitter.emit(
                LineageEvent(
                    event_type=COMPLETE,
                    run_id=ctx.run_id,
                    job_name=self.name,
                    inputs=lin_inputs,
                    outputs=lin_outputs,
                    records_read=result.records_read,
                    records_written=result.records_written,
                )
            )
        except Exception as exc:
            result.error = exc
            metrics.counter(ERRORS_TOTAL).add(1, {**attrs, "phase": "run"})
            run_span.set_attribute("success", False)
            run_span.record_exception(exc)
            emitter.emit(
                LineageEvent(
                    event_type=FAIL,
                    run_id=ctx.run_id,
                    job_name=self.name,
                    inputs=lin_inputs,
                    outputs=lin_outputs,
                    error=str(exc),
                )
            )
            self._fire("on_error", ctx, exc)
            raise
        finally:
            result.duration_seconds = time.monotonic() - start
            metrics.histogram(DURATION_SECONDS).record(result.duration_seconds, attrs)
            run_span.set_attribute("duration_seconds", result.duration_seconds)
            run_span.end()
            self._fire("post_run", ctx, result)

        return result

    def _is_dag(self) -> bool:
        """True if this pipeline uses any Task-DAG feature (ADR-0028).

        Plain multi-task pipelines with no dependencies / branches / custom
        trigger rules keep the simple sequential semantics (raise on first
        failure) for backward compatibility.
        """
        return any(
            t.depends_on or t.branch or t.trigger_rule != DEFAULT_TRIGGER_RULE for t in self.tasks
        )

    def _execute_task(
        self,
        task: Task,
        conns: dict[str, Connector],
        cursor_from: CursorValue,
        cursor_to: CursorValue,
        *,
        cursored: bool,
        ctx: Context,
        result: RunResult,
        metrics: Any,
        tracer: Any,
        attrs: dict[str, Any],
        task_runner: Callable[..., tuple[int, int, CursorValue]],
    ) -> tuple[int, int]:
        """Run one task fully: span + runner + metric/result accumulation + hooks."""
        if cursored and not task.cursor_column:
            raise TaskError(
                f"Task {task.name or task.source!r}: cursor_column is required "
                "when Pipeline.run is called with cursor_from/cursor_to."
            )
        self._fire("on_task_start", ctx, task)
        task_span = tracer.start_span(
            "pipeline.task",
            attributes={
                "pipeline": self.name,
                "task": task.name or task.source or "unknown",
                "source": task.source or "",
                "sink": ",".join(s.name for s in task.effective_sinks()),
            },
        )
        try:
            read_count, write_count, task_max = task_runner(task, conns, cursor_from, cursor_to)
            task_span.set_attribute("records_read", read_count)
            task_span.set_attribute("records_written", write_count)
        except Exception as exc:
            task_span.record_exception(exc)
            raise
        finally:
            task_span.end()
        result.records_read += read_count
        result.records_written += write_count
        if task_max is not None and (
            result.new_cursor is None or task_max > result.new_cursor  # type: ignore[operator]
        ):
            result.new_cursor = task_max
        metrics.counter(RECORDS_READ_TOTAL).add(read_count, attrs)
        metrics.counter(RECORDS_WRITTEN_TOTAL).add(write_count, attrs)
        self._fire("on_task_end", ctx, task, write_count)
        return read_count, write_count

    def _run_dag(
        self,
        ordered: list[Task],
        conns: dict[str, Connector],
        cursor_from: CursorValue,
        cursor_to: CursorValue,
        *,
        cursored: bool,
        ctx: Context,
        result: RunResult,
        metrics: Any,
        tracer: Any,
        attrs: dict[str, Any],
        task_runner: Callable[..., tuple[int, int, CursorValue]],
    ) -> None:
        """Execute a Task-orchestration DAG (ADR-0028).

        Sequential, in topological order. Tracks per-task state, applies trigger
        rules + branch selection (with skip propagation), and — unlike the plain
        path — keeps running independent tasks after one fails, raising the first
        error at the end so the whole run is reported failed.
        """
        states: dict[str, str] = {}
        deselected: set[str] = set()  # direct downstreams a branch did not pick
        first_error: BaseException | None = None
        # name → tasks that depend on it (direct downstreams), for branch skip.
        downstream: dict[str, set[str]] = {t.name: set() for t in ordered if t.name}
        for t in ordered:
            for dep in t.depends_on:
                if t.name:
                    downstream.setdefault(dep, set()).add(t.name)

        for task in ordered:
            name = task.name or ""
            if name in deselected:
                states[name] = TASK_SKIPPED
                continue
            decision = self._eval_trigger_rule(task, states)
            if decision == TASK_SKIPPED:
                states[name] = TASK_SKIPPED
                continue
            if decision == TASK_UPSTREAM_FAILED:
                states[name] = TASK_UPSTREAM_FAILED
                continue
            try:
                read, written = self._execute_task(
                    task,
                    conns,
                    cursor_from,
                    cursor_to,
                    cursored=cursored,
                    ctx=ctx,
                    result=result,
                    metrics=metrics,
                    tracer=tracer,
                    attrs=attrs,
                    task_runner=task_runner,
                )
                states[name] = TASK_SUCCESS
                if task.branch:
                    selected = set(self._branch_select(task, read=read, written=written))
                    for child in downstream.get(name, set()) - selected:
                        deselected.add(child)
            except Exception as exc:
                states[name] = TASK_FAILED
                metrics.counter(ERRORS_TOTAL).add(1, {**attrs, "phase": "run"})
                if first_error is None:
                    first_error = exc

        result.task_states = states
        if first_error is not None:
            raise first_error

    @staticmethod
    def _eval_trigger_rule(task: Task, states: dict[str, str]) -> str:
        """Decide whether ``task`` runs given its upstream states.

        Returns one of ``TASK_SUCCESS`` (= run it), ``TASK_SKIPPED``, or
        ``TASK_UPSTREAM_FAILED``. All upstream tasks are already terminal because
        execution is in topological order.
        """
        ups = task.depends_on
        if not ups:
            return TASK_SUCCESS  # root task — always eligible
        up_states = [states.get(u, TASK_SKIPPED) for u in ups]
        failed = any(s in (TASK_FAILED, TASK_UPSTREAM_FAILED) for s in up_states)
        skipped = any(s == TASK_SKIPPED for s in up_states)
        any_success = any(s == TASK_SUCCESS for s in up_states)
        rule = task.trigger_rule
        if rule == "all_done":
            return TASK_SUCCESS
        if rule == "one_success":
            return TASK_SUCCESS if any_success else TASK_SKIPPED
        if rule == "none_failed":
            return TASK_UPSTREAM_FAILED if failed else TASK_SUCCESS
        # default: all_success
        if failed:
            return TASK_UPSTREAM_FAILED
        if skipped:
            return TASK_SKIPPED
        return TASK_SUCCESS

    @staticmethod
    def _branch_select(task: Task, *, read: int, written: int) -> list[str]:
        """First-match branch selection — returns the chosen downstream names."""
        env = {"records_read": read, "records_written": written, "success": True}
        for rule in task.branch:
            if rule.when is None:
                return rule.to
            try:
                # Sandboxed (no builtins), same pattern as the filter transform.
                ok = eval(compile(rule.when, "<branch:when>", "eval"), {"__builtins__": {}}, env)
            except Exception as exc:
                raise TaskError(f"task {task.name!r}: branch 'when' failed: {exc}") from exc
            if ok:
                return rule.to
        return []

    def _run_graph_task(
        self,
        task: Task,
        connectors: dict[str, Connector],
        cursor_from: CursorValue,
        cursor_to: CursorValue,
    ) -> tuple[int, int, CursorValue]:
        """Execute a dataflow graph by materializing each node (ADR-0041).

        Nodes run in topological order; each node's output records are held in
        memory and consumed by its downstream nodes. Sources are read once (no
        per-sink re-read), transforms map their single input, ``join`` nodes
        hash-join their inputs (fan-in), and sinks write their input. Per-edge
        ``when`` predicates filter the records flowing along that edge.
        Multi-source and join are supported. Reading each source fully before
        any sink writes also sidesteps the same-connection deadlock (ADR-0034).

        records_read sums all source reads; records_written sums all sink
        writes. Cursor backfill is not yet supported for graphs.
        """
        if cursor_from is not None or cursor_to is not None:
            raise TaskError("graph pipelines do not support cursor backfill yet")

        by_id = {n.id: n for n in task.graph_nodes}
        incoming: dict[str, list[GraphEdge]] = {n.id: [] for n in task.graph_nodes}
        for e in task.graph_edges:
            if e.to_id not in by_id or e.from_id not in by_id:
                raise TaskError(f"graph edge references unknown node: {e.from_id}→{e.to_id}")
            incoming[e.to_id].append(e)

        # Precompile edge predicates once, keyed by edge identity.
        compiled: dict[int, CodeType] = {}
        for e in task.graph_edges:
            if e.when is not None:
                try:
                    compiled[id(e)] = compile(e.when, "<edge:when>", "eval")
                except SyntaxError as exc:
                    raise TaskError(f"edge {e.from_id}→{e.to_id}: invalid 'when': {exc}") from exc

        outputs: dict[str, list[Record]] = {}

        def _edge_records(edge: GraphEdge) -> list[Record]:
            records = outputs[edge.from_id]
            code = compiled.get(id(edge))
            if code is None:
                return records
            kept: list[Record] = []
            for rec in records:
                try:
                    ok = bool(
                        eval(
                            code,
                            {"__builtins__": {}},
                            {"data": rec.data, "metadata": rec.metadata},
                        )
                    )
                except Exception as exc:
                    raise TransformError(
                        f"edge {edge.from_id}→{edge.to_id}: 'when' failed: {exc}"
                    ) from exc
                if ok:
                    kept.append(rec)
            return kept

        def _inputs(node_id: str) -> list[list[Record]]:
            # One filtered record-list per incoming edge, in declaration order.
            return [_edge_records(e) for e in incoming[node_id]]

        order = _toposort_nodes(by_id, task.graph_edges)
        records_read = 0
        written = 0

        for node_id in order:
            node = by_id[node_id]
            if node.kind == "source":
                if not node.source_name:
                    raise TaskError(f"graph source node {node.id!r} has no connection")
                source = connectors.get(node.source_name)
                if source is None:
                    raise TaskError(f"No connector for graph source '{node.source_name}'")
                if not isinstance(source, BatchSource):
                    raise TaskError(f"Graph source '{node.source_name}' is not a BatchSource")
                recs = list(source.read(query=node.query, **node.source_options))
                records_read += len(recs)
                outputs[node.id] = recs
            elif node.kind == "transform":
                ins = _inputs(node.id)
                if len(ins) != 1:
                    raise TaskError(f"transform node {node.id!r} takes exactly one input")
                out_recs: list[Record] = []
                for rec in ins[0]:
                    result = node.transform_fn(rec) if node.transform_fn is not None else rec
                    if result is not None:
                        out_recs.append(result)
                outputs[node.id] = out_recs
            elif node.kind == "join":
                ins = _inputs(node.id)
                if len(ins) < 2:
                    raise TaskError(f"join node {node.id!r} needs at least two inputs")
                outputs[node.id] = _hash_join(ins, node.join_on or [], node.join_how)
            elif node.kind == "sink":
                ins = _inputs(node.id)
                if len(ins) != 1:
                    raise TaskError(f"sink node {node.id!r} takes exactly one input")
                if node.sink is None:
                    raise TaskError(f"graph sink node {node.id!r} has no sink spec")
                spec = node.sink
                sink = connectors.get(spec.name)
                if sink is None:
                    raise TaskError(f"No connector for graph sink '{spec.name}'")
                if not isinstance(sink, BatchSink):
                    raise TaskError(f"Graph sink '{spec.name}' is not a BatchSink")
                written += sink.write(
                    iter(ins[0]),
                    mode=spec.mode,
                    key_columns=spec.key_columns,
                    table=spec.table,
                    **spec.options,
                )
                outputs[node.id] = ins[0]
            else:
                raise TaskError(f"graph node {node.id!r} has unknown kind {node.kind!r}")

        return records_read, written, None

    def _run_pre_sql(self, task: Task, connectors: dict[str, Connector]) -> None:
        """Execute the task's pre-load SQL actions once, in order (ADR-0035)."""
        for action in task.pre_sql:
            conn = connectors.get(action.connection)
            if conn is None:
                raise TaskError(
                    f"No connector instance provided for pre-SQL connection "
                    f"'{action.connection}'"
                )
            if not isinstance(conn, SqlExecutor):
                raise TaskError(
                    f"Connection '{action.connection}' does not support SQL execution "
                    f"(no execute_statement); cannot run pre-load SQL"
                )
            conn.execute_statement(action.statement)

    def _run_task(
        self,
        task: Task,
        connectors: dict[str, Connector],
        cursor_from: CursorValue = None,
        cursor_to: CursorValue = None,
    ) -> tuple[int, int, CursorValue]:
        if task.graph_nodes:
            return self._run_graph_task(task, connectors, cursor_from, cursor_to)
        if not task.source:
            raise TaskError(f"Task missing source: {task!r}")
        sink_specs = task.effective_sinks()
        if not sink_specs:
            raise TaskError(f"Task missing sink: {task!r}")

        source = connectors.get(task.source)
        if source is None:
            raise TaskError(f"No connector instance provided for source '{task.source}'")
        if not isinstance(source, BatchSource):
            raise TaskError(f"Source '{task.source}' is not a BatchSource")

        sinks: list[tuple[SinkSpec, BatchSink]] = []
        for spec in sink_specs:
            sink = connectors.get(spec.name)
            if sink is None:
                raise TaskError(f"No connector instance provided for sink '{spec.name}'")
            if not isinstance(sink, BatchSink):
                raise TaskError(f"Sink '{spec.name}' is not a BatchSink")
            sinks.append((spec, sink))

        # Pre-load SQL (ADR-0035): run once, before reading, so a DELETE clears
        # the target rows this run re-inserts → delete-then-insert idempotency.
        self._run_pre_sql(task, connectors)

        records_read = 0
        new_cursor: CursorValue = None
        dlq_enabled = self.dlq is not None
        metrics = get_metrics()

        cursored = cursor_from is not None or cursor_to is not None
        cursor_col = task.cursor_column if cursored else None

        def _source_iter() -> Iterator[Record]:
            if cursor_col is not None:
                yield from source.read_since(
                    cursor_col,
                    cursor_from,
                    query=task.query,
                    **task.source_options,
                )
            else:
                yield from source.read(query=task.query, **task.source_options)

        def _read_and_transform(
            count: bool = True,
            accept: Callable[[Record], bool] | None = None,
        ) -> Iterator[Record]:
            nonlocal records_read, new_cursor
            for raw in _source_iter():
                # Inclusive upper bound — skip rows beyond cursor_to. We still
                # consume them from the iterator so a non-ordered source doesn't
                # block on a buffered tail; ``read_since`` contracts ordering
                # ascending, so an ordered source will see strictly increasing
                # values and won't yield anything past cursor_to once the
                # break triggers (we ``return`` below to short-circuit).
                if cursor_col is not None:
                    cv = raw.data.get(cursor_col)
                    # cv is Any (Record.data is dict[str, Any]); the
                    # caller is responsible for picking a column whose
                    # values are mutually comparable with cursor_to /
                    # new_cursor.
                    if cursor_to is not None and cv is not None and cv > cursor_to:
                        # Ordering is ascending — anything after this is also > cursor_to.
                        return
                    if count and cv is not None and (new_cursor is None or cv > new_cursor):
                        new_cursor = cv
                if count:
                    records_read += 1
                record: Record | None = raw
                try:
                    for fn in task.transforms:
                        if record is None:
                            break
                        record = fn(record)
                except Exception as exc:
                    if dlq_enabled:
                        metrics.counter(ERRORS_TOTAL).add(
                            1, {"pipeline": self.name, "phase": "transform", "routed": "dlq"}
                        )
                        self._dlq_route_batch(connectors, raw)
                        continue
                    raise TransformError(f"transform {fn!r} failed on record {raw!r}") from exc
                if record is not None:
                    # Conditional routing (ADR-0027): a per-sink ``accept``
                    # decides whether this transformed record belongs to the
                    # sink currently being written.
                    if accept is not None and not accept(record):
                        continue
                    yield record

        # Conditional routing setup (ADR-0027). Compile each sink's ``when``
        # predicate once; a record routes to the FIRST conditional sink whose
        # predicate is truthy, otherwise to every default (``when``-less) sink.
        # No ``when`` anywhere ⇒ pure fan-out (every sink gets every record).
        compiled_when: dict[int, CodeType] = {}
        for i, (spec, _sink) in enumerate(sinks):
            if spec.when is not None:
                try:
                    compiled_when[i] = compile(spec.when, "<sink:when>", "eval")
                except SyntaxError as exc:
                    raise TaskError(
                        f"sink '{spec.name}': cannot compile routing 'when': {exc}"
                    ) from exc
        conditional_indices = sorted(compiled_when)
        has_routing = bool(conditional_indices)

        def _match(record: Record) -> int | None:
            for idx in conditional_indices:
                try:
                    # Sandboxed (no builtins), same pattern as the filter transform.
                    ok = eval(
                        compiled_when[idx],
                        {"__builtins__": {}},
                        {"data": record.data, "metadata": record.metadata},
                    )
                except Exception as exc:
                    raise TransformError(
                        f"sink '{sinks[idx][0].name}': routing 'when' failed: {exc}"
                    ) from exc
                if ok:
                    return idx
            return None

        def _accept_for(index: int) -> Callable[[Record], bool]:
            is_default = sinks[index][0].when is None

            def accept(record: Record) -> bool:
                matched = _match(record)
                if matched is None:
                    return is_default
                return matched == index

            return accept

        # RDBMS sinks (sqlite/postgres/mysql) require ``table`` as a
        # keyword; without it ``write`` raises ``WriteError``. The YAML
        # builder strips ``table`` from ``sink_options`` and stores it
        # on ``task.sink_table``, so we re-thread it here. (Stream sinks
        # like Kafka pull it from ``sink_options['topic']`` directly —
        # see ``_run_task_stream`` below.)
        #
        # Fan-out (ADR-0026): with multiple sinks we re-read the source once
        # per sink rather than buffering, preserving streaming/bounded memory.
        # records_read / new_cursor are counted on the first pass only; cursor_to
        # filtering still applies on every pass.
        written = 0
        for i, (spec, sink) in enumerate(sinks):
            accept = _accept_for(i) if has_routing else None
            written += sink.write(
                _read_and_transform(count=(i == 0), accept=accept),
                mode=spec.mode,
                key_columns=spec.key_columns,
                table=spec.table,
                pre_sql=spec.pre_sql,
                **spec.options,
            )
        return records_read, written, new_cursor

    def _fire(self, event: str, *args: Any) -> None:
        for hook in self._hooks.get(event, []):
            hook(*args)

    # ---------- internal helpers ------------------------------------------

    def _retry_kwargs(self) -> dict[str, Any]:
        """Translate ``self.retry`` (RetryConfig) into ``@retryable`` kwargs."""
        rc = self.retry
        if rc is None:
            return {}
        out: dict[str, Any] = {
            "max_attempts": rc.max_attempts,
            "backoff": rc.backoff,
            "initial_delay_seconds": rc.initial_delay_seconds,
        }
        if rc.max_delay_seconds is not None:
            out["max_delay_seconds"] = rc.max_delay_seconds
        return out

    def _dlq_route_batch(
        self,
        connectors: dict[str, Connector],
        record: Record,
    ) -> None:
        """Best-effort write the offending record to the DLQ BatchSink."""
        if self.dlq is None:
            return
        sink = connectors.get(self.dlq.connection)
        if not isinstance(sink, BatchSink):
            return
        with contextlib.suppress(Exception):
            sink.write([record], mode=self.dlq.mode)

    async def _dlq_route_stream(
        self,
        connectors: dict[str, Connector],
        record: Record,
    ) -> None:
        """Best-effort publish the offending record to the DLQ StreamSink."""
        if self.dlq is None:
            return
        sink = connectors.get(self.dlq.connection)
        if not isinstance(sink, StreamSink):
            return
        topic = self.dlq.topic or "dlq"
        with contextlib.suppress(Exception):
            await sink.publish(topic, record)

    # ---------------- stream runtime (Step 3.2) ---------------------------

    async def arun_stream(
        self,
        context: Context | None = None,
        *,
        connectors: dict[str, Connector] | None = None,
        stop_after_records: int | None = None,
        stop_after_seconds: float | None = None,
    ) -> RunResult:
        """Run a stream pipeline (mode=='stream') until a stop condition fires.

        Stop conditions (any of):
          * ``stop_after_records`` — total records consumed across all tasks
          * ``stop_after_seconds`` — wall time since the call started
          * The async iterator returned by ``source.subscribe`` is exhausted
          * The task is cancelled (``KeyboardInterrupt`` / ``CancelledError``)
        """
        if self.mode != "stream":
            raise PipelineError(f"arun_stream is stream-only — for mode={self.mode!r} use run()")

        ctx = context or Context(pipeline_name=self.name)
        conns = connectors or {}
        start = time.monotonic()
        metrics = get_metrics()
        attrs = {"pipeline": self.name, "mode": self.mode}
        result = RunResult(run_id=ctx.run_id, pipeline_name=self.name, success=False)

        self._fire("pre_run", ctx)
        try:
            for task in self.tasks:
                self._fire("on_task_start", ctx, task)
                read_count, write_count = await self._arun_stream_task(
                    task,
                    conns,
                    stop_after_records=stop_after_records,
                    stop_after_seconds=stop_after_seconds,
                    started_at=start,
                )
                result.records_read += read_count
                result.records_written += write_count
                metrics.counter(RECORDS_READ_TOTAL).add(read_count, attrs)
                metrics.counter(RECORDS_WRITTEN_TOTAL).add(write_count, attrs)
                self._fire("on_task_end", ctx, task, write_count)
            result.success = True
        except Exception as exc:
            result.error = exc
            metrics.counter(ERRORS_TOTAL).add(1, {**attrs, "phase": "run"})
            self._fire("on_error", ctx, exc)
            raise
        finally:
            result.duration_seconds = time.monotonic() - start
            metrics.histogram(DURATION_SECONDS).record(result.duration_seconds, attrs)
            self._fire("post_run", ctx, result)
        return result

    async def _arun_stream_task(
        self,
        task: Task,
        connectors: dict[str, Connector],
        *,
        stop_after_records: int | None,
        stop_after_seconds: float | None,
        started_at: float,
    ) -> tuple[int, int]:
        if not task.source:
            raise TaskError(f"Stream task missing source: {task!r}")
        sink_specs = task.effective_sinks()
        if not sink_specs:
            raise TaskError(f"Stream task missing sink: {task!r}")
        if len(sink_specs) > 1:
            raise TaskError(f"Stream fan-out to multiple sinks is not supported: {task!r}")
        sink_spec = sink_specs[0]

        source = connectors.get(task.source)
        sink = connectors.get(sink_spec.name)
        if source is None:
            raise TaskError(f"No connector instance provided for source '{task.source}'")
        if sink is None:
            raise TaskError(f"No connector instance provided for sink '{sink_spec.name}'")
        if not isinstance(source, StreamSource):
            raise TaskError(f"Source '{task.source}' is not a StreamSource")
        if not isinstance(sink, StreamSink):
            raise TaskError(f"Sink '{sink_spec.name}' is not a StreamSink")

        topic_in = task.source_options.get("topic") or task.query
        if not topic_in:
            raise TaskError(
                f"stream source '{task.source}' requires 'topic' (in source.topic or source.query)"
            )
        group_id = task.source_options.get("group_id")
        topic_out = sink_spec.options.get("topic") or sink_spec.table
        if not topic_out:
            raise TaskError(
                f"stream sink '{sink_spec.name}' requires 'topic' (in sink.topic or sink.table)"
            )

        buffer = sink_spec.options.get("buffer") or {}
        max_records = int(buffer.get("max_records", 1) or 1)
        max_seconds = float(buffer.get("max_seconds", 0.0) or 0.0)

        records_read = 0
        records_written = 0
        pending = 0
        last_flush = time.monotonic()
        dlq_enabled = self.dlq is not None
        metrics = get_metrics()

        # Optionally wrap sink.publish with a retry policy.
        publish_fn = sink.publish
        if self.retry is not None:
            publish_fn = retryable(**self._retry_kwargs())(publish_fn)

        async def _flush_and_commit() -> None:
            nonlocal pending, last_flush
            await sink.flush()
            pending = 0
            last_flush = time.monotonic()
            if self.commit_strategy == "after_sink_flush":
                with contextlib.suppress(NotImplementedError):
                    await source.commit()

        try:
            async for raw in source.subscribe(topic_in, group_id=group_id):
                records_read += 1
                record: Record | None = raw
                try:
                    for fn in task.transforms:
                        if record is None:
                            break
                        record = fn(record)
                except Exception as exc:
                    if dlq_enabled:
                        metrics.counter(ERRORS_TOTAL).add(
                            1,
                            {"pipeline": self.name, "phase": "transform", "routed": "dlq"},
                        )
                        await self._dlq_route_stream(connectors, raw)
                        continue
                    raise TransformError(f"transform {fn!r} failed on record {raw!r}") from exc

                if record is not None:
                    await publish_fn(topic_out, record)
                    records_written += 1
                    pending += 1

                if pending >= max_records or (
                    max_seconds > 0 and (time.monotonic() - last_flush) >= max_seconds
                ):
                    await _flush_and_commit()

                if stop_after_records is not None and records_read >= stop_after_records:
                    break
                if (
                    stop_after_seconds is not None
                    and (time.monotonic() - started_at) >= stop_after_seconds
                ):
                    break
        finally:
            if pending:
                with contextlib.suppress(Exception):
                    await _flush_and_commit()

        return records_read, records_written
