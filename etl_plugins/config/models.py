"""Pydantic config models for connections.yaml / pipelines/*.yaml. SPEC.md §5."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConnectionConfig(BaseModel):
    """One connection definition.

    ``type`` identifies the connector in ``ConnectorRegistry``. All other fields
    are connector-specific (host, port, account, bootstrap_servers, ...), so
    ``extra="allow"`` is intentional.
    """

    model_config = ConfigDict(extra="allow")

    type: str

    def options(self) -> dict[str, Any]:
        """Return all non-``type`` fields as a dict (use to instantiate a Connector)."""
        return self.model_dump(exclude={"type"})


class ConnectionsConfig(BaseModel):
    """Top-level structure of ``configs/connections.yaml``."""

    model_config = ConfigDict(extra="forbid")

    connections: dict[str, ConnectionConfig] = Field(default_factory=dict)


class SourceConfig(BaseModel):
    """Pipeline source definition. ``connection`` references a key in connections.yaml."""

    model_config = ConfigDict(extra="allow")

    connection: str
    query: str | None = None
    chunk_size: int = 10_000
    # Incremental / backfill cursor (Step 6.1). When set, ``Pipeline.run`` with
    # ``cursor_from`` / ``cursor_to`` reads via ``BatchSource.read_since`` on this
    # column. Required for the backfill action (ADR-0039); the source connector
    # must implement ``read_since``.
    cursor_column: str | None = None
    # topic, group_id, format 등은 extra=allow로 통과


class SinkConfig(BaseModel):
    """Pipeline sink definition."""

    model_config = ConfigDict(extra="allow")

    connection: str
    table: str | None = None
    mode: str = "append"  # append | overwrite | upsert
    key_columns: list[str] | None = None
    # Conditional routing predicate (ADR-0027). A sandboxed Python expression
    # (``data`` / ``metadata`` in scope, no builtins) evaluated per record. When
    # set, this sink only receives records the expression accepts; routing uses
    # first-match across the sink list, with ``when``-less sinks as the default.
    when: str | None = None
    # Phase VV (ADR-0066, 2026-05-29): cross-DB replication. When ``True``
    # and the sink connector implements :class:`SchemaWriter` *and* the
    # source connector implements :class:`SchemaInspector`, the pipeline
    # creates the sink table from the source's column schema before the
    # first write — translated through :mod:`etl_plugins.core.type_mapping`
    # so e.g. a postgres ``BIGINT`` becomes a sqlite ``INTEGER`` and
    # round-trips cleanly. Default ``False`` keeps the existing behaviour
    # (sink table must already exist).
    auto_create_table: bool = False
    # Phase AAA (ADR-0071, 2026-05-29): collision policy used when
    # ``auto_create_table`` is True and the sink table already exists.
    # ``skip`` (default) leaves the existing table; ``drop`` rebuilds
    # from the current source schema (DESTRUCTIVE — use for nightly
    # snapshot-style replication or dev sandboxes); ``error`` fails the
    # run so an operator can resolve the conflict by hand.
    auto_create_if_exists: Literal["skip", "drop", "error"] = "skip"


class TransformConfig(BaseModel):
    """A single transform step. ``type`` dispatches to a transform implementation."""

    model_config = ConfigDict(extra="allow")

    type: str  # rename | cast | filter | python | ...


class RetryConfig(BaseModel):
    """Retry policy (used by Step 3 retry decorator)."""

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = 3
    backoff: str = "exponential"  # fixed | exponential
    initial_delay_seconds: float = 5.0
    max_delay_seconds: float | None = None


class MetricsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    namespace: str = "etl_plugins"


class TracingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    exporter: str = "otlp"


class ObservabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    tracing: TracingConfig = Field(default_factory=TracingConfig)


class BufferConfig(BaseModel):
    """Stream sink buffering policy."""

    model_config = ConfigDict(extra="forbid")

    max_records: int = 10_000
    max_seconds: float = 30.0


class CommitConfig(BaseModel):
    """Stream commit strategy (SPEC.md §5.5)."""

    model_config = ConfigDict(extra="forbid")

    strategy: str = "after_sink_flush"  # at_least_once | after_sink_flush | ...


class DlqConfig(BaseModel):
    """Dead-letter queue routing (SPEC.md §9.1).

    ``connection`` references a sink in ``connections.yaml``. Records whose
    transforms raise are routed here instead of failing the pipeline. ``table``
    is used for BatchSink DLQs; ``topic`` is used for StreamSink DLQs.
    """

    model_config = ConfigDict(extra="forbid")

    connection: str
    table: str | None = None
    topic: str | None = None
    mode: str = "append"


TRIGGER_RULES = frozenset({"all_success", "all_done", "one_success", "none_failed"})


class BranchRuleConfig(BaseModel):
    """One branch rule (ADR-0028). ``when`` is a sandboxed Python predicate over
    the task outcome (``records_read``/``records_written``/``success``); ``None``
    is the default/else. ``to`` lists the direct downstream tasks to select."""

    model_config = ConfigDict(extra="forbid")

    when: str | None = None
    to: list[str] = Field(default_factory=list)


class TaskConfig(BaseModel):
    """One task in a Task-orchestration DAG (ADR-0028).

    A task is the single-task pipeline shape (source → transforms → sink(s))
    plus a ``name`` and ``depends_on`` edges. Pipelines either use the top-level
    single-task fields (backward compatible) or a ``tasks`` list of these.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    source: SourceConfig
    transforms: list[TransformConfig] = Field(default_factory=list)
    sink: SinkConfig | None = None
    sinks: list[SinkConfig] = Field(default_factory=list)
    # Upstream task names that must complete before this task runs.
    depends_on: list[str] = Field(default_factory=list)
    # When this task runs given its upstream states (see ``TRIGGER_RULES``).
    trigger_rule: str = "all_success"
    # Branch selection rules — non-empty makes this a branch task.
    branch: list[BranchRuleConfig] = Field(default_factory=list)
    # Per-task retry override (자유도 2단계, 2026-06-17). When set, this task
    # uses its own retry policy instead of the pipeline-level ``retry``
    # (Airflow per-task ``retries``/``retry_delay``). ``None`` → fall back to
    # the pipeline default.
    retry: RetryConfig | None = None
    # Per-task execution timeout in seconds (Airflow ``execution_timeout``).
    # Checked at record/chunk boundaries during the read loop — a slow task
    # is failed with TaskTimeoutError (retried if a retry policy applies).
    # ``None`` → fall back to the pipeline's ``task_timeout_seconds``.
    timeout_seconds: float | None = None
    # Dynamic task mapping (자유도 3단계, ADR-0098 — Airflow ``.expand()``).
    # ``name → list`` (or a ``{{ }}`` template resolving to a list); the task
    # fans out into one instance per element (cross product over keys), each
    # exposing ``{{ map.<key> }}``. Empty ⇒ not a mapped task.
    expand: dict[str, Any] = Field(default_factory=dict)
    # Explicit XCom push (자유도 3단계 f/u, ADR-0097). ``key → {column, distinct?}``;
    # publishes the list of that column's values from the rows this task
    # processes under ``xcom.<task>.<key>``, so a downstream task can
    # ``expand`` over it (``{{ xcom.discover.regions }}``). Without this only
    # the auto summary (records_read/written/success/new_cursor) is on XCom —
    # none of which is a fan-out list. Forces the records data path.
    push_xcom: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_sink(self) -> TaskConfig:
        if self.sink is not None and self.sinks:
            raise ValueError(f"task {self.name!r}: specify either 'sink' or 'sinks', not both")
        if self.sink is None and not self.sinks:
            raise ValueError(f"task {self.name!r}: needs a 'sink' or non-empty 'sinks'")
        if self.trigger_rule not in TRIGGER_RULES:
            raise ValueError(
                f"task {self.name!r}: unknown trigger_rule {self.trigger_rule!r} "
                f"(allowed: {sorted(TRIGGER_RULES)})"
            )
        for key, spec in self.push_xcom.items():
            if not isinstance(spec, dict) or "column" not in spec:
                raise ValueError(
                    f"task {self.name!r}: push_xcom[{key!r}] needs a 'column' "
                    "(the row column whose values are published as a list)"
                )
            extra = set(spec) - {"column", "distinct"}
            if extra:
                raise ValueError(
                    f"task {self.name!r}: push_xcom[{key!r}] has unknown keys "
                    f"{sorted(extra)} (allowed: 'column', 'distinct')"
                )
        return self

    def effective_sinks(self) -> list[SinkConfig]:
        """Normalised sink list — single ``sink`` becomes a one-element list."""
        return [self.sink] if self.sink is not None else self.sinks


# ``sql_exec`` is a standalone side-effect node (ADR-0042 follow-up,
# 2026-05-26 user request "Run SQL 또한 ... SOURCE와 통합한 형태로
# 제공"): runs a SQL statement against the named connection at execution
# time, emits zero records. Structurally it behaves like a source (indegree
# 0 enforced below) but has its own dispatch in :func:`execute_graph_node`
# that calls ``execute_statement`` instead of ``read()``.
GRAPH_NODE_TYPES = frozenset({"source", "transform", "sink", "join", "aggregate", "sql_exec"})
JOIN_HOWS = frozenset({"inner", "left", "right", "outer"})
AGG_OPS = frozenset({"count", "sum", "min", "max", "avg"})


class AggregationConfig(BaseModel):
    """One aggregation in an ``aggregate`` node (ADR-0041, G3).

    ``op`` is count | sum | min | max | avg over ``column`` (``count`` may omit
    ``column`` to count rows); ``name`` is the output column.
    """

    model_config = ConfigDict(extra="forbid")

    op: str
    column: str | None = None
    name: str

    @model_validator(mode="after")
    def _check(self) -> AggregationConfig:
        if self.op not in AGG_OPS:
            raise ValueError(f"unknown aggregation op {self.op!r} (allowed: {sorted(AGG_OPS)})")
        if self.op != "count" and not self.column:
            raise ValueError(f"aggregation {self.name!r}: op {self.op!r} requires a 'column'")
        return self


class GraphNodeConfig(BaseModel):
    """One node in a dataflow graph (ADR-0030, generalized in ADR-0041).

    ``type`` is ``source`` | ``transform`` | ``sink`` | ``join`` | ``aggregate``.
    Source/sink carry their connector fields (``extra=allow``); a ``transform``
    nests a :class:`TransformConfig`; a ``join`` is a fan-in node (≥2 incoming)
    merging inputs on ``on`` keys with ``how``; an ``aggregate`` groups its single
    input by ``group_by`` and emits one record per group with ``aggregations``.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    # source / sink / sql_exec
    connection: str | None = None
    query: str | None = None  # source
    table: str | None = None  # sink
    mode: str = "append"  # sink
    key_columns: list[str] | None = None  # sink
    # sql_exec — the SQL the node runs at execution time.
    statement: str | None = None
    # transform
    transform: TransformConfig | None = None
    # join (fan-in) — merge ≥2 inputs on ``on`` keys (ADR-0041)
    on: list[str] | None = None
    how: str = "inner"
    # aggregate — group ``group_by`` keys, emit one record per group (ADR-0041)
    group_by: list[str] | None = None
    aggregations: list[AggregationConfig] | None = None

    @model_validator(mode="after")
    def _check_node(self) -> GraphNodeConfig:
        if self.type not in GRAPH_NODE_TYPES:
            raise ValueError(
                f"graph node {self.id!r}: unknown type {self.type!r} "
                f"(allowed: {sorted(GRAPH_NODE_TYPES)})"
            )
        if self.type == "join" and self.how not in JOIN_HOWS:
            raise ValueError(
                f"join node {self.id!r}: unknown how {self.how!r} (allowed: {sorted(JOIN_HOWS)})"
            )
        if self.type == "aggregate" and not self.aggregations:
            raise ValueError(f"aggregate node {self.id!r}: needs at least one aggregation")
        if self.type == "sql_exec":
            if not self.connection:
                raise ValueError(f"sql_exec node {self.id!r}: 'connection' is required")
            if not self.statement:
                raise ValueError(f"sql_exec node {self.id!r}: 'statement' is required")
        return self


class GraphEdgeConfig(BaseModel):
    """A directed edge ``from_node → to_node``; records flow along it.

    Optional ``when`` is a sandboxed Python predicate (``data`` / ``metadata``
    in scope, no builtins) — a record traverses the edge only when it's truthy.
    Branching = a node with several outgoing edges, each gated by its ``when``.
    """

    model_config = ConfigDict(extra="forbid")

    from_node: str
    to_node: str
    when: str | None = None


class GraphConfig(BaseModel):
    """A dataflow graph: nodes + edges (ADR-0030, generalized in ADR-0041).

    The unified pipeline model — every shape lowers into one of these
    (see :func:`etl_plugins.runtime.graph.to_graph`). A free DAG:

    * ≥1 ``source`` (indegree 0) and ≥1 ``sink`` (indegree 1);
    * ``transform`` nodes have exactly one incoming edge;
    * ``join`` nodes are the fan-in points (≥2 incoming edges);
    * the graph is acyclic.

    Fan-in only happens at an explicit ``join`` node, so a regular transform's
    semantics stay unambiguous (one input stream). Multi-source + join
    *execution* lands with the materialize engine (ADR-0041, G2); G1 validates
    the shape so the model and builder can be built against it first.
    """

    model_config = ConfigDict(extra="forbid")

    nodes: list[GraphNodeConfig] = Field(default_factory=list)
    edges: list[GraphEdgeConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_graph(self) -> GraphConfig:
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("graph node ids must be unique")
        id_set = set(ids)
        # ADR-0042 follow-up (2026-05-26): the "must have ≥1 source AND ≥1
        # sink" requirement is dropped. A pipeline made entirely of a
        # standalone side-effect source (e.g. ``sql_exec`` running a
        # MERGE) is legitimate; a pipeline made of pure-source reads
        # with no sink (e.g. cache-warming via HTTP polling) is too.
        # The runtime is unchanged — sources still must have 0 incoming
        # edges and non-source non-join nodes still need exactly one,
        # so the structural integrity of the graph is preserved.

        indegree: dict[str, int] = dict.fromkeys(ids, 0)
        adjacency: dict[str, list[str]] = {i: [] for i in ids}
        for e in self.edges:
            if e.from_node not in id_set or e.to_node not in id_set:
                raise ValueError(f"edge references unknown node: {e.from_node}→{e.to_node}")
            indegree[e.to_node] += 1
            adjacency[e.from_node].append(e.to_node)

        for n in self.nodes:
            deg = indegree[n.id]
            if n.type == "source" or n.type == "sql_exec":
                # sql_exec behaves like a source structurally: it produces a
                # (zero-record) stream and accepts no upstream input.
                if deg != 0:
                    raise ValueError(f"{n.type} node {n.id!r} must have no incoming edges")
            elif n.type == "join":
                if deg < 2:
                    raise ValueError(
                        f"join node {n.id!r} must have at least two incoming edges "
                        f"(got {deg}); use a transform/sink for single-input nodes"
                    )
            elif deg != 1:
                # transform / sink take exactly one input stream — fan-in goes
                # through an explicit ``join`` node so semantics stay unambiguous.
                raise ValueError(
                    f"node {n.id!r} ({n.type}) must have exactly one incoming edge "
                    f"(got {deg}); merge multiple inputs with a 'join' node"
                )

        # Acyclic (Kahn) — fan-in makes cycles possible, so detect them.
        remaining = dict(indegree)
        queue = [i for i, d in remaining.items() if d == 0]
        visited = 0
        while queue:
            cur = queue.pop()
            visited += 1
            for nxt in adjacency[cur]:
                remaining[nxt] -= 1
                if remaining[nxt] == 0:
                    queue.append(nxt)
        if visited != len(ids):
            raise ValueError("graph has a cycle")
        return self


class PipelineConfig(BaseModel):
    """Top-level structure of ``configs/pipelines/*.yaml``.

    Three shapes, mutually exclusive:

    * **single-task** (backward compatible) — top-level ``source`` + ``transforms``
      + ``sink``/``sinks``. The common 1-source pipeline.
    * **DAG** (ADR-0028) — a ``tasks`` list of :class:`TaskConfig` wired by
      ``depends_on``.
    * **graph** (ADR-0030) — a dataflow ``graph`` of operator nodes + edges,
      with per-edge branching. Records flow through the graph.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    mode: str = "batch"  # batch | stream
    schedule: str | None = None
    # Pipeline-local variables (ADR-0041, V1). Referenced as ``${var.name}`` in
    # string fields; resolved at load time (see config.variables). Workspace-wide
    # globals merge underneath these (locals win) at the service layer (V2).
    variables: dict[str, Any] = Field(default_factory=dict)
    # Run parameters (자유도 1단계, 2026-06-15). Declared *default* values a run
    # can template against via the runtime ``{{ params.name }}`` layer
    # (etl_plugins.runtime.templating). Distinct from ``variables`` (static,
    # ``${var.x}``, load time): params are *per-run*, overridable at trigger
    # time (CLI ``--param k=v`` / API trigger body), so the same pipeline runs
    # for different dates/regions/windows without editing the config.
    params: dict[str, Any] = Field(default_factory=dict)
    # Node-level execution (ADR-0041, H2, service-only). When true *and* this is a
    # graph pipeline, the worker expands the graph into per-node ``node_runs`` and
    # executes node-by-node (per-node status / retry foundation) instead of one
    # whole-graph pass. Opt-in; the core ignores it.
    node_level: bool = False
    # Asset-driven orchestration (ADR-0037). When true, the service auto-enqueues
    # a run of this pipeline whenever an upstream run materializes one of its
    # input assets (Dagster auto-materialize / Airflow Dataset trigger). Opt-in
    # to avoid surprising cascades; the core ignores it (service-only behaviour).
    auto_materialize: bool = False
    # Freshness SLA in minutes (ADR-0038, service-only). When set, the scheduler
    # re-runs this pipeline if its output assets go staler than this (or were
    # never materialized) — Dagster freshness-based auto-materialize. None = off.
    freshness_sla_minutes: int | None = None
    # --- single-task shape ---
    source: SourceConfig | None = None
    transforms: list[TransformConfig] = Field(default_factory=list)
    # Exactly one of ``sink`` (single) or ``sinks`` (fan-out) — ADR-0026.
    sink: SinkConfig | None = None
    sinks: list[SinkConfig] = Field(default_factory=list)
    # --- DAG shape (ADR-0028) ---
    tasks: list[TaskConfig] = Field(default_factory=list)
    # --- dataflow graph shape (ADR-0030) ---
    graph: GraphConfig | None = None
    retry: RetryConfig | None = None
    # Default per-task execution timeout (자유도 2단계). Applied to any task
    # that doesn't set its own ``timeout_seconds``. ``None`` → no timeout.
    task_timeout_seconds: float | None = None
    observability: ObservabilityConfig | None = None
    commit: CommitConfig | None = None
    dlq: DlqConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_engine(cls, data: object) -> object:
        # The Spark execution backend + ``engine`` field were removed (ADR-0040);
        # local in-process execution is the only model now. Tolerate the stale
        # ``engine`` key on configs persisted before the removal so they keep
        # loading under ``extra="forbid"`` (the value is simply ignored).
        if isinstance(data, dict):
            data.pop("engine", None)
        return data

    @model_validator(mode="after")
    def _check_shape(self) -> PipelineConfig:
        has_single = self.source is not None
        has_dag = bool(self.tasks)
        has_graph = self.graph is not None
        if sum([has_single, has_dag, has_graph]) > 1:
            raise ValueError("specify exactly one of: single-task fields, 'tasks', or 'graph'")
        if not (has_single or has_dag or has_graph):
            raise ValueError(
                "a pipeline needs a 'source' (+ sink), a non-empty 'tasks', or a 'graph'"
            )
        if has_single:
            if self.sink is not None and self.sinks:
                raise ValueError("specify either 'sink' or 'sinks', not both")
            if self.sink is None and not self.sinks:
                raise ValueError("a pipeline needs a 'sink' or non-empty 'sinks'")
        else:
            # DAG / graph: top-level single-task fields must be absent.
            if self.sink is not None or self.sinks or self.transforms:
                raise ValueError(
                    "with 'tasks' or 'graph', put source/transforms/sink inside the "
                    "tasks/nodes, not at the top level"
                )
        return self

    def effective_sinks(self) -> list[SinkConfig]:
        """Single-task sink list. Only valid for the single-task shape."""
        return [self.sink] if self.sink is not None else self.sinks

    def effective_tasks(self) -> list[TaskConfig]:
        """Normalised task list — the single-task shape becomes one TaskConfig."""
        if self.tasks:
            return self.tasks
        assert self.source is not None  # guaranteed by _check_shape
        return [
            TaskConfig(
                name=self.name,
                source=self.source,
                transforms=self.transforms,
                sink=self.sink,
                sinks=self.sinks,
            )
        ]
