"""Pydantic config models for connections.yaml / pipelines/*.yaml. SPEC.md §5."""

from __future__ import annotations

from typing import Any

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
        return self

    def effective_sinks(self) -> list[SinkConfig]:
        """Normalised sink list — single ``sink`` becomes a one-element list."""
        return [self.sink] if self.sink is not None else self.sinks


class GraphNodeConfig(BaseModel):
    """One node in a dataflow graph (ADR-0030).

    ``type`` is ``source`` | ``transform`` | ``sink``. Source/sink carry their
    connector fields (``extra=allow`` for connector-specific options); a
    transform nests a :class:`TransformConfig` under ``transform``.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    # source / sink
    connection: str | None = None
    query: str | None = None  # source
    table: str | None = None  # sink
    mode: str = "append"  # sink
    key_columns: list[str] | None = None  # sink
    # transform
    transform: TransformConfig | None = None


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
    """A dataflow graph: nodes + edges (ADR-0030).

    v1 is a source-rooted tree (one source, branching/fan-out, no fan-in), so
    every non-source node has exactly one incoming edge and each sink has a
    unique path back to the source.
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
        sources = [n for n in self.nodes if n.type == "source"]
        sinks = [n for n in self.nodes if n.type == "sink"]
        if len(sources) != 1:
            raise ValueError("graph must have exactly one source node (v1)")
        if not sinks:
            raise ValueError("graph must have at least one sink node")
        indegree: dict[str, int] = dict.fromkeys(ids, 0)
        for e in self.edges:
            if e.from_node not in id_set or e.to_node not in id_set:
                raise ValueError(f"edge references unknown node: {e.from_node}→{e.to_node}")
            indegree[e.to_node] += 1
        for n in self.nodes:
            if n.type == "source":
                if indegree[n.id] != 0:
                    raise ValueError(f"source node {n.id!r} must have no incoming edges")
            elif indegree[n.id] != 1:
                # tree invariant — fan-in/joins are out of scope for v1
                raise ValueError(
                    f"node {n.id!r} must have exactly one incoming edge "
                    f"(got {indegree[n.id]}); fan-in is not supported yet"
                )
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
    # Execution backend (ADR-0031). "local" = row-streaming (default); "spark"
    # compiles the DAG to Spark. Validated at run time by the backend registry
    # (config layer stays unaware of which backends exist).
    engine: str = "local"
    # Asset-driven orchestration (ADR-0037). When true, the service auto-enqueues
    # a run of this pipeline whenever an upstream run materializes one of its
    # input assets (Dagster auto-materialize / Airflow Dataset trigger). Opt-in
    # to avoid surprising cascades; the core ignores it (service-only behaviour).
    auto_materialize: bool = False
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
    observability: ObservabilityConfig | None = None
    commit: CommitConfig | None = None
    dlq: DlqConfig | None = None

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
