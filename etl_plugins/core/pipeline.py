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
from typing import Any, cast

import structlog

from etl_plugins.config.models import DlqConfig, RetryConfig
from etl_plugins.core.arrow import ArrowReadable, ArrowWritable
from etl_plugins.core.asset import (
    AssetKey,
    AssetLineage,
    LineageEdge,
    asset_kind,
    derive_asset_key,
)
from etl_plugins.core.column_lineage import ColumnLineage
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

_module_logger = structlog.get_logger(__name__)

TransformFn = Callable[[Record], Record | None]
"""Transform: takes a Record, returns the (possibly modified) Record, or None to drop it."""

DatasetTransformFn = Callable[[Iterator[Record]], Iterator[Record]]
"""Dataset-level transform (ADR-0093): consumes the WHOLE record stream and
yields a new one — joins/aggregations/windows that row-level ``TransformFn``
cannot express. Built by ``runtime.transforms`` (e.g. the DuckDB ``sql``
transform) and marked with ``fn.dataset_transform = True``. Batch mode only:
an unbounded stream has no "whole dataset"."""

AnyTransformFn = TransformFn | DatasetTransformFn
"""Either transform flavour; tell them apart with :func:`is_dataset_transform`."""


def is_dataset_transform(fn: AnyTransformFn) -> bool:
    """True when ``fn`` is a dataset-level transform (stream-in → stream-out)."""
    return getattr(fn, "dataset_transform", False) is True


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
    # Cross-DB replication (Phase VV / ADR-0066, 2026-05-29). When ``True``
    # and the source connector implements :class:`SchemaInspector` and the
    # sink connector implements :class:`SchemaWriter`, the pipeline
    # creates the sink table from the source's schema before the first
    # write — translated through :mod:`etl_plugins.core.type_mapping`.
    auto_create_table: bool = False
    # Phase AAA (ADR-0071, 2026-05-29): forwarded to
    # ``SchemaWriter.ensure_table(if_exists=...)``.
    auto_create_if_exists: str = "skip"


@dataclass(frozen=True)
class SqlAction:
    """A SQL statement run once, before the load, against ``connection``
    (ADR-0035). Used for delete-then-insert idempotency — e.g. clearing the
    target rows this run will re-insert. The connection must resolve to a
    connector implementing :class:`~etl_plugins.core.sql_exec.SqlExecutor`."""

    connection: str
    statement: str


@dataclass(frozen=True)
class AggSpec:
    """One aggregation in an ``aggregate`` node (ADR-0041). ``op`` is
    count|sum|min|max|avg over ``column`` (count may omit it); ``name`` is the
    output column."""

    op: str
    name: str
    column: str | None = None


@dataclass
class GraphNode:
    """One node in a dataflow graph (ADR-0030, join/aggregate added in ADR-0041)."""

    id: str
    # source | transform | sink | join | aggregate | sql_exec.
    # sql_exec was added in ADR-0042 follow-up (2026-05-26) — a standalone
    # side-effect node that runs a SQL statement and emits zero records.
    kind: str
    # source / sql_exec
    source_name: str | None = None
    query: str | None = None
    source_options: dict[str, Any] = field(default_factory=dict)
    # sql_exec — the SQL the node runs at execution time.
    sql_statement: str | None = None
    # transform — row-level or dataset-level (ADR-0093)
    transform_fn: AnyTransformFn | None = None
    # sink
    sink: SinkSpec | None = None
    # join (fan-in, ADR-0041) — merge ≥2 inputs on ``join_on`` keys
    join_on: list[str] | None = None
    join_how: str = "inner"
    # aggregate (ADR-0041) — group ``agg_group_by`` keys, emit one record/group
    agg_group_by: list[str] | None = None
    aggregations: list[AggSpec] = field(default_factory=list)


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
    transforms: list[AnyTransformFn] = field(default_factory=list)
    # Original TransformConfig dumps for introspection-only callers (e.g.
    # ``_auto_create_sink_tables`` projecting columns through declarative
    # transforms). ``transforms`` holds the compiled callables; we keep
    # the raw specs alongside so we don't have to re-parse the config.
    transform_specs: list[dict[str, Any]] = field(default_factory=list)
    # SQL statements run once before the load (ADR-0035) — e.g. a DELETE to make
    # an append re-runnable (delete-then-insert). Run in order, before reading.
    pre_sql: list[SqlAction] = field(default_factory=list)
    sink: str | None = None
    sink_table: str | None = None
    sink_mode: str = "append"
    sink_key_columns: list[str] | None = None
    sink_options: dict[str, Any] = field(default_factory=dict)
    sink_pre_sql: str | None = None
    # Phase VV (ADR-0066, 2026-05-29): flat-sink mirror of
    # ``SinkSpec.auto_create_table``. Builder forwards from
    # ``SinkConfig.auto_create_table`` so single-sink configs also opt
    # into cross-DB schema replication.
    sink_auto_create_table: bool = False
    sink_auto_create_if_exists: str = "skip"
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
                auto_create_table=self.sink_auto_create_table,
                auto_create_if_exists=self.sink_auto_create_if_exists,
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

    def transform(self, fn: AnyTransformFn) -> Task:
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


def _project_columns_through_transforms(source_columns: list[Any], task: Task) -> list[Any]:
    """Phase XX (ADR-0068): simulate the declarative transform chain
    over the source column list to obtain the *post-transform* column
    schema. Used by :meth:`Pipeline._auto_create_sink_tables` so the
    sink table is created with the columns the write will actually
    produce — not the verbatim source columns.

    Handled transform types: ``rename`` (key swap), ``add_constant``
    (append new column with ``TEXT`` fallback type), ``cast`` (no shape
    change), ``drop`` (key removal), ``select`` (keep listed columns
    only), ``filter`` / ``dedupe`` / ``assert`` (row-level, no shape
    change). Any other transform (``python`` / ``custom_python`` /
    ``sql_exec`` / unknown) is treated as opaque — we return ``[]`` so
    the caller falls back to the raw source columns. That preserves the
    Phase VV behaviour for opaque chains.
    """
    from etl_plugins.core.inspect import ColumnInfo

    by_name: dict[str, ColumnInfo] = {c.name: c for c in source_columns}
    order: list[str] = [c.name for c in source_columns]

    # ``Task.transforms`` holds the compiled callables; the original
    # config sits on ``task.transform_specs`` (set by the builder for
    # exactly this kind of introspection). When unavailable, no chain
    # is applied — caller falls back to raw source columns.
    specs: list[dict[str, Any]] = list(getattr(task, "transform_specs", []) or [])
    if not specs:
        return source_columns

    for spec in specs:
        ttype = spec.get("type")
        if ttype == "rename":
            mapping = spec.get("mapping") or {}
            new_order: list[str] = []
            new_by_name: dict[str, ColumnInfo] = {}
            for name in order:
                new_name = mapping.get(name, name)
                col = by_name[name]
                new_by_name[new_name] = ColumnInfo(name=new_name, type=col.type)
                new_order.append(new_name)
            order, by_name = new_order, new_by_name
        elif ttype == "drop":
            gone = set(spec.get("columns") or [])
            order = [n for n in order if n not in gone]
            by_name = {n: by_name[n] for n in order}
        elif ttype == "select":
            keep = set(spec.get("columns") or [])
            order = [n for n in order if n in keep]
            by_name = {n: by_name[n] for n in order}
        elif ttype == "add_constant":
            col_name = spec.get("column")
            if col_name and col_name not in by_name:
                # We don't know the literal's vendor type — default to
                # TEXT, which any sink accepts. The user can declare a
                # ``cast`` after the ``add_constant`` if they want a
                # specific type.
                by_name[col_name] = ColumnInfo(name=col_name, type="TEXT")
                order.append(col_name)
        elif ttype == "cast":
            casts = spec.get("columns") or {}
            for col_name, target_type in casts.items():
                if col_name in by_name:
                    by_name[col_name] = ColumnInfo(name=col_name, type=str(target_type))
        elif ttype in {"filter", "dedupe", "assert"}:
            continue  # row-level; no shape change
        else:
            # Unknown or opaque transform — bail out so caller falls
            # back to raw source columns (Phase VV behaviour).
            return []
    return [by_name[n] for n in order]


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


def _agg_value(op: str, values: list[Any]) -> Any:
    """Reduce non-null ``values`` with ``op`` (count|sum|min|max|avg)."""
    if op == "count":
        return len(values)
    present = [v for v in values if v is not None]
    if not present:
        return None
    if op == "sum":
        return sum(present)
    if op == "min":
        return min(present)
    if op == "max":
        return max(present)
    if op == "avg":
        return sum(present) / len(present)
    raise TaskError(f"unknown aggregation op {op!r}")


def _aggregate(records: list[Record], group_by: list[str], specs: list[AggSpec]) -> list[Record]:
    """Group ``records`` by ``group_by`` keys, emit one record per group.

    Each output carries the group-key columns plus one column per spec. Empty
    ``group_by`` aggregates the whole input into a single record.
    """
    groups: dict[tuple[Any, ...], list[Record]] = {}
    order: list[tuple[Any, ...]] = []
    for rec in records:
        try:
            key = tuple(rec.data[k] for k in group_by)
        except KeyError as exc:
            raise TransformError(f"aggregate group_by key {exc} missing") from exc
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(rec)

    out: list[Record] = []
    for key in order:
        members = groups[key]
        data: dict[str, Any] = dict(zip(group_by, key, strict=True))
        for spec in specs:
            if spec.op == "count":
                data[spec.name] = len(members)  # count(*) — row count per group
            else:
                col = spec.column
                values = [m.data.get(col) for m in members] if col is not None else []
                data[spec.name] = _agg_value(spec.op, values)
        out.append(Record(data=data, metadata=members[0].metadata))
    return out


def apply_edge_predicate(records: list[Record], when: str | None) -> list[Record]:
    """Filter ``records`` by an edge ``when`` predicate (sandboxed, no builtins).

    ``data`` / ``metadata`` are in scope. ``None`` passes everything through.
    Shared by the in-process graph engine and node-level execution (ADR-0041).
    """
    if when is None:
        return records
    try:
        code = compile(when, "<edge:when>", "eval")
    except SyntaxError as exc:
        raise TaskError(f"edge predicate {when!r} is invalid: {exc}") from exc
    kept: list[Record] = []
    for rec in records:
        try:
            ok = bool(
                eval(code, {"__builtins__": {}}, {"data": rec.data, "metadata": rec.metadata})
            )
        except Exception as exc:
            raise TransformError(f"edge 'when' {when!r} failed: {exc}") from exc
        if ok:
            kept.append(rec)
    return kept


@dataclass
class NodeResult:
    """Outcome of executing one graph node (ADR-0041). ``output`` feeds downstream
    nodes; counters roll up into the run's totals."""

    output: list[Record]
    records_read: int = 0
    records_written: int = 0


def execute_graph_node(
    node: GraphNode,
    inputs: list[list[Record]],
    connectors: dict[str, Connector],
) -> NodeResult:
    """Run one graph node given its already-filtered inputs (one list per incoming
    edge, in edge-declaration order). Operator dispatch shared by the in-process
    materialize engine (:meth:`Pipeline._run_graph_task`) and node-level execution
    (ADR-0041, H2). Edge ``when`` filtering happens before this (see
    :func:`apply_edge_predicate`)."""
    if node.kind == "source":
        if not node.source_name:
            raise TaskError(f"graph source node {node.id!r} has no connection")
        source = connectors.get(node.source_name)
        if source is None:
            raise TaskError(f"No connector for graph source '{node.source_name}'")
        if not isinstance(source, BatchSource):
            raise TaskError(f"Graph source '{node.source_name}' is not a BatchSource")
        recs = list(source.read(query=node.query, **node.source_options))
        return NodeResult(output=recs, records_read=len(recs))
    if node.kind == "sql_exec":
        # Standalone SQL-execution node (ADR-0042 follow-up). Runs the
        # statement against the named connection and emits zero records;
        # downstream nodes (if any) see an empty stream. Reuses the same
        # ``SqlExecutor`` capability the sink ``pre_sql`` mechanism uses
        # so behaviour is identical regardless of where the SQL runs.
        if not node.source_name:
            raise TaskError(f"sql_exec node {node.id!r} has no connection")
        if not node.sql_statement:
            raise TaskError(f"sql_exec node {node.id!r} has no statement")
        target = connectors.get(node.source_name)
        if target is None:
            raise TaskError(f"No connector for sql_exec '{node.source_name}'")
        if not isinstance(target, SqlExecutor):
            raise TaskError(
                f"sql_exec connection {node.source_name!r} does not support "
                f"execute_statement (must implement SqlExecutor)"
            )
        target.execute_statement(node.sql_statement)
        return NodeResult(output=[], records_read=0)
    if node.kind == "transform":
        if len(inputs) != 1:
            raise TaskError(f"transform node {node.id!r} takes exactly one input")
        # Dataset-level transform (ADR-0093): hand it the whole input stream.
        if node.transform_fn is not None and is_dataset_transform(node.transform_fn):
            dataset_fn = cast("DatasetTransformFn", node.transform_fn)
            return NodeResult(output=list(dataset_fn(iter(inputs[0]))))
        row_fn = cast("TransformFn | None", node.transform_fn)
        out_recs: list[Record] = []
        for rec in inputs[0]:
            result = row_fn(rec) if row_fn is not None else rec
            if result is not None:
                out_recs.append(result)
        return NodeResult(output=out_recs)
    if node.kind == "join":
        if len(inputs) < 2:
            raise TaskError(f"join node {node.id!r} needs at least two inputs")
        return NodeResult(output=_hash_join(inputs, node.join_on or [], node.join_how))
    if node.kind == "aggregate":
        if len(inputs) != 1:
            raise TaskError(f"aggregate node {node.id!r} takes exactly one input")
        return NodeResult(output=_aggregate(inputs[0], node.agg_group_by or [], node.aggregations))
    if node.kind == "sink":
        if len(inputs) != 1:
            raise TaskError(f"sink node {node.id!r} takes exactly one input")
        if node.sink is None:
            raise TaskError(f"graph sink node {node.id!r} has no sink spec")
        spec = node.sink
        sink = connectors.get(spec.name)
        if sink is None:
            raise TaskError(f"No connector for graph sink '{spec.name}'")
        if not isinstance(sink, BatchSink):
            raise TaskError(f"Graph sink '{spec.name}' is not a BatchSink")
        written = sink.write(
            iter(inputs[0]),
            mode=spec.mode,
            key_columns=spec.key_columns,
            table=spec.table,
            **spec.options,
        )
        return NodeResult(output=inputs[0], records_written=written)
    raise TaskError(f"graph node {node.id!r} has unknown kind {node.kind!r}")


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
    # ADR-0041 K5b: static column lineage derived at build time from the
    # source ``PipelineConfig`` so emitters (e.g. OpenLineage) can attach a
    # ``columnLineage`` facet to output datasets without re-parsing the
    # config. None ⇒ not derived (Pipeline constructed manually in tests
    # / older builds) — emitters fall back to table-level lineage only.
    column_lineage: ColumnLineage | None = None
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
        # ADR-0041 K5b: column lineage piggybacks on the same event so OL
        # emitters can attach a ``columnLineage`` facet without re-deriving.
        # Built by ``runtime/builder.build_pipeline``; ``None`` for Pipelines
        # constructed by hand (tests / older callers).
        col_lin = self.column_lineage
        emitter.emit(
            LineageEvent(
                event_type=START,
                run_id=ctx.run_id,
                job_name=self.name,
                inputs=lin_inputs,
                outputs=lin_outputs,
                column_lineage=col_lin,
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
                    column_lineage=col_lin,
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

        outputs: dict[str, list[Record]] = {}

        def _inputs(node_id: str) -> list[list[Record]]:
            # One edge-filtered record-list per incoming edge, in declaration order.
            return [apply_edge_predicate(outputs[e.from_id], e.when) for e in incoming[node_id]]

        records_read = 0
        written = 0
        for node_id in _toposort_nodes(by_id, task.graph_edges):
            node = by_id[node_id]
            result = execute_graph_node(node, _inputs(node_id), connectors)
            outputs[node_id] = result.output
            records_read += result.records_read
            written += result.records_written

        return records_read, written, None

    def _auto_create_sink_tables(
        self,
        task: Task,
        source: Connector,
        sinks: list[tuple[SinkSpec, BatchSink]],
    ) -> None:
        """Phase VV (ADR-0066) + Phase XX (ADR-0068): create any sink
        tables flagged with ``auto_create_table=True``, anticipating
        the *post-transform* shape.

        Walk:

        1. find the source's table name (``task.source_options['table']``
           or the first ``FROM`` clause of ``task.query``);
        2. ask the source for its raw columns via the optional
           :class:`SchemaInspector` capability;
        3. **Phase XX**: simulate the declarative transform chain
           (``rename`` / ``add_constant`` / ``cast`` / ``drop`` /
           ``select``) over those columns to get the *projected*
           column set + type per column. Non-declarative transforms
           (``python`` / ``custom_python`` / ``sql_exec``) preserve
           whatever they don't explicitly touch — we keep the columns
           we know about and let the eventual write surface
           mismatches.
        4. call :class:`SchemaWriter.ensure_table` with the projected
           columns. The sink renders vendor types through
           :mod:`etl_plugins.core.type_mapping`.

        Best-effort everywhere: missing capabilities, un-parseable
        query, missing source table all degrade to "no auto-create"
        rather than raising. A failed write later in the pipeline gives
        the user a much clearer error than a half-built schema would.
        """
        from etl_plugins.core.inspect import SchemaInspector, SchemaWriter

        wanted = [(spec, sink) for spec, sink in sinks if spec.auto_create_table]
        if not wanted:
            return
        if not isinstance(source, SchemaInspector):
            return

        src_table = task.source_options.get("table") if task.source_options else None
        if not src_table and task.query:
            try:
                from etl_plugins.runtime.sql_lineage import extract_referenced_tables

                tables = extract_referenced_tables(task.query)
                src_table = tables[0] if tables else None
            except Exception:
                src_table = None
        if not src_table:
            return

        try:
            source_columns = source.list_columns(src_table)
        except Exception:
            return
        if not source_columns:
            return

        projected = _project_columns_through_transforms(source_columns, task)

        for spec, sink in wanted:
            if not isinstance(sink, SchemaWriter):
                continue
            if not spec.table:
                continue
            # Best-effort: a connector-specific quirk shouldn't abort
            # the run before a write has even been tried. The sink's
            # write will raise a clearer "no such table" if the DDL
            # silently failed.
            cols_for_sink = projected if projected else source_columns
            # Phase AAC (ADR-0072): when the sink is an upsert target,
            # forward its ``key_columns`` as the table's primary key so
            # ``ON CONFLICT`` / ``ON DUPLICATE KEY UPDATE`` has the
            # required uniqueness constraint to attach to. Skipped for
            # non-upsert modes — append / overwrite don't need it.
            pk_for_sink = (
                list(spec.key_columns) if spec.mode == "upsert" and spec.key_columns else None
            )
            # Phase AAR (2026-06-01) — Postgres-style drivers leave the
            # current transaction in *aborted* state after a failed
            # DDL ("current transaction is aborted, commands ignored
            # until end of transaction block"). The previous
            # contextlib.suppress hid the DDL failure but the sink's
            # subsequent write tripped over the poisoned transaction.
            # We catch + log + rollback so the next stage sees a
            # clean connection.
            try:
                sink.ensure_table(
                    spec.table,
                    cols_for_sink,
                    if_exists=spec.auto_create_if_exists,
                    primary_key=pk_for_sink,
                )
            except Exception as exc:
                log = getattr(self, "_log", None) or _module_logger
                log.warning(
                    "auto_create_table.failed",
                    sink_table=spec.table,
                    error=str(exc)[:300],
                )
                # Best-effort rollback. Both psycopg and pymssql expose
                # ``rollback()`` directly; sqlite's ``connection`` is
                # always rollback-safe. If the connector has no
                # rollback (HTTP / Kafka / S3 don't), skipping is fine.
                # The conn lookup itself is wrapped in suppress because
                # some connectors (postgres' ``connection`` property)
                # raise ``ConnectError`` when the underlying ``_conn``
                # is None — getattr's default doesn't catch that.
                with contextlib.suppress(Exception):
                    conn = getattr(sink, "_conn", None)
                    if conn is None:
                        conn = getattr(sink, "connection", None)
                    if conn is not None:
                        conn.rollback()

    def _run_pre_sql(self, task: Task, connectors: dict[str, Connector]) -> None:
        """Execute the task's pre-load SQL actions once, in order (ADR-0035)."""
        for action in task.pre_sql:
            conn = connectors.get(action.connection)
            if conn is None:
                raise TaskError(
                    f"No connector instance provided for pre-SQL connection '{action.connection}'"
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

        # Phase VV (ADR-0066, 2026-05-29): cross-DB replication. Any sink
        # spec with ``auto_create_table=True`` triggers a one-shot
        # ``source.list_columns(...) → sink.ensure_table(...)`` before
        # the first read. We translate vendor type strings between
        # dialects in ``ensure_table`` itself (per :mod:`type_mapping`).
        self._auto_create_sink_tables(task, source, sinks)

        # Pre-load SQL (ADR-0035): run once, before reading, so a DELETE clears
        # the target rows this run re-inserts → delete-then-insert idempotency.
        self._run_pre_sql(task, connectors)

        cursored_run = cursor_from is not None or cursor_to is not None
        fast = self._try_arrow_fast_path(task, source, sinks, cursored=cursored_run)
        if fast is not None:
            return fast

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

        def _cursor_stream(count: bool) -> Iterator[Record]:
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
                yield raw

        # ADR-0093: transforms compose as STAGES — runs of row-level fns
        # (applied per record, DLQ-routable) separated by dataset-level fns
        # (which wrap the whole stream: joins/aggregations/windows, e.g. the
        # DuckDB ``sql`` transform). A pipeline without dataset transforms is
        # exactly one row stage — the historical behaviour, unchanged.
        stages: list[list[TransformFn] | DatasetTransformFn] = []
        for tfn in task.transforms:
            if is_dataset_transform(tfn):
                stages.append(cast("DatasetTransformFn", tfn))
            else:
                row_tfn = cast("TransformFn", tfn)
                last = stages[-1] if stages else None
                if isinstance(last, list):
                    last.append(row_tfn)
                else:
                    stages.append([row_tfn])

        def _apply_row_stage(stream: Iterator[Record], fns: list[TransformFn]) -> Iterator[Record]:
            for raw in stream:
                record: Record | None = raw
                try:
                    for fn in fns:
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
                    yield record

        def _read_and_transform(
            count: bool = True,
            accept: Callable[[Record], bool] | None = None,
        ) -> Iterator[Record]:
            stream = _cursor_stream(count)
            for stage in stages:
                stream = (
                    _apply_row_stage(stream, stage) if isinstance(stage, list) else stage(stream)
                )
            for record in stream:
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

    def _try_arrow_fast_path(
        self,
        task: Task,
        source: BatchSource,
        sinks: list[tuple[SinkSpec, BatchSink]],
        *,
        cursored: bool,
    ) -> tuple[int, int, CursorValue] | None:
        """Bulk Arrow path (ADR-0093 P2b): bypass the Record plane entirely.

        Eligible when nothing in the task needs per-record Python: no
        transforms (row or dataset), no cursor window, exactly one sink
        with no ``when`` routing, mode append/overwrite, and BOTH
        connectors declare the Arrow capabilities. Returns ``None`` to
        fall through to the Record path. Semantics are identical for
        eligible tasks — DLQ only fires on transform errors, and there
        are none here.
        """
        if task.transforms or cursored or len(sinks) != 1:
            return None
        spec, sink = sinks[0]
        if spec.when is not None or spec.mode not in ("append", "overwrite"):
            return None
        if not isinstance(source, ArrowReadable) or not isinstance(sink, ArrowWritable):
            return None

        _module_logger.info("arrow_fast_path", pipeline=self.name, task=task.name, mode=spec.mode)
        records_read = 0

        def _counted() -> Iterator[Any]:
            nonlocal records_read
            for batch in source.read_arrow(query=task.query, **task.source_options):
                records_read += batch.num_rows
                yield batch

        written = sink.write_arrow(
            _counted(),
            mode=spec.mode,
            key_columns=spec.key_columns,
            table=spec.table,
            pre_sql=spec.pre_sql,
            **spec.options,
        )
        return records_read, written, None

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
        """Best-effort write the offending record to the DLQ BatchSink.

        Phase II (ADR-0053, 2026-05-29): forward ``table`` from
        :class:`DlqConfig` to the sink. Without this kwarg a sink like
        :class:`SQLiteConnector` raises ``WriteError("requires 'table'")``
        and the ``contextlib.suppress`` below silently dropped every
        bad record — DLQ promised partial-success but delivered
        nothing. Bug surfaced by the dogfood scenario.
        """
        if self.dlq is None:
            return
        sink = connectors.get(self.dlq.connection)
        if not isinstance(sink, BatchSink):
            return
        write_kwargs: dict[str, Any] = {"mode": self.dlq.mode}
        if self.dlq.table is not None:
            write_kwargs["table"] = self.dlq.table
        with contextlib.suppress(Exception):
            sink.write([record], **write_kwargs)

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

        # Dataset-level transforms need the WHOLE dataset; an unbounded
        # stream never has one (ADR-0093).
        if any(is_dataset_transform(fn) for fn in task.transforms):
            raise TaskError(
                "dataset-level transforms (e.g. 'sql') require batch mode — "
                "an unbounded stream has no complete dataset to query"
            )
        row_transforms = cast("list[TransformFn]", task.transforms)

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
                    for fn in row_transforms:
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
