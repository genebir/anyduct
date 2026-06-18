"""Pipeline + connector instantiation from config.

The composition root:

    1. Load ``connections.yaml`` → ``ConnectionsConfig``
    2. Load ``pipelines/<x>.yaml`` → ``PipelineConfig``
    3. Resolve each connection through :class:`ConnectorRegistry` and instantiate
       it with the ``options()`` dict.
    4. Build a :class:`Pipeline` with a single :class:`Task` matching the YAML.
    5. Return ``(pipeline, connectors_dict)`` — caller manages open/close
       (or use :func:`run_pipeline_yaml`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from etl_plugins.config.loader import load_connections, load_pipeline
from etl_plugins.config.models import (
    ConnectionConfig,
    ConnectionsConfig,
    GraphConfig,
    PipelineConfig,
    TaskConfig,
)
from etl_plugins.config.secrets import SecretBackend
from etl_plugins.core.connector import Connector
from etl_plugins.core.exceptions import ConfigError
from etl_plugins.core.pipeline import (
    AggSpec,
    BranchRule,
    GraphEdge,
    GraphNode,
    Pipeline,
    SinkSpec,
    SqlAction,
    Task,
)
from etl_plugins.core.registry import ConnectorRegistry
from etl_plugins.runtime.column_lineage import derive_column_lineage
from etl_plugins.runtime.templating import RuntimeContext, render_config_templates
from etl_plugins.runtime.transforms import build_transform


def build_connector(name: str, config: ConnectionConfig) -> Connector:
    """Instantiate the registered Connector class for ``config.type``."""
    klass = ConnectorRegistry.get(config.type)
    try:
        return klass(**config.options())
    except TypeError as exc:
        raise ConfigError(
            f"connection {name!r}: failed to construct {klass.__name__} "
            f"with {sorted(config.options())}: {exc}"
        ) from exc


def build_connectors(config: ConnectionsConfig) -> dict[str, Connector]:
    """Instantiate every connection defined in ``connections.yaml``."""
    return {name: build_connector(name, c) for name, c in config.connections.items()}


_SINK_OPTS_EXCLUDE = {"connection", "table", "mode", "key_columns", "when", "pre_sql"}

# Suffix for the dedicated sink-side connector instance created when a sink
# reuses a connection that is also a source. See ``_dedicated_sink_key``.
_SINK_ROLE_SUFFIX = "::sink"


def _dedicated_sink_key(
    conn_name: str,
    *,
    source_names: set[str],
    connectors: dict[str, Connector],
    factory: Callable[[str], Connector] | None,
) -> str:
    """Pick the connectors-dict key a sink should use.

    A pipeline that both reads from and writes to the *same* connection would
    otherwise share a single driver connection for the streaming read cursor
    and the write — which deadlocks on drivers that serialize connection
    access (e.g. psycopg's COPY vs. a server-side cursor). When a sink reuses a
    source's connection and a ``factory`` is available, mint a *second*
    connector instance (a separate physical connection) under a synthetic key
    so read and write never contend. Preserves bounded-memory streaming — no
    buffering. Without a factory the original key is kept (callers that supply
    pre-built connectors, e.g. single-connection drivers that tolerate
    concurrent read/write, or tests).
    """
    if conn_name in source_names and factory is not None:
        key = f"{conn_name}{_SINK_ROLE_SUFFIX}"
        if key not in connectors:
            connectors[key] = factory(conn_name)
        return key
    return conn_name


def _build_task(
    task_cfg: TaskConfig,
    *,
    pipeline_name: str,
    mode: str,
    connectors: dict[str, Connector],
    connector_factory: Callable[[str], Connector] | None = None,
) -> Task:
    """Build one runtime :class:`Task` from a :class:`TaskConfig`."""
    label = f"pipeline {pipeline_name!r} task {task_cfg.name!r}"

    # Operator kinds (ADR-0099): ``sql`` / ``proc_call`` are pure orchestration
    # steps — no source/sink, run a statement / call a procedure against
    # ``connection``. Build a minimal Task; the core dispatches on ``kind``.
    if task_cfg.kind in ("sql", "proc_call"):
        if task_cfg.connection not in connectors:
            raise ConfigError(
                f"{label}: connection {task_cfg.connection!r} "
                f"not in available connectors {sorted(connectors)}"
            )
        return Task(
            name=task_cfg.name,
            kind=task_cfg.kind,
            op_connection=task_cfg.connection,
            statements=list(task_cfg.statements),
            procedure=task_cfg.procedure,
            proc_args=list(task_cfg.args),
            depends_on=list(task_cfg.depends_on),
            trigger_rule=task_cfg.trigger_rule,
            retry=task_cfg.retry,
            timeout_seconds=task_cfg.timeout_seconds,
            expand=dict(task_cfg.expand),
        )

    src = task_cfg.source
    if src is None:  # pragma: no cover - guarded by TaskConfig validator
        raise ConfigError(f"{label}: kind 'etl' needs a source")
    sink_cfgs = task_cfg.effective_sinks()

    if src.connection not in connectors:
        raise ConfigError(
            f"{label}: source connection {src.connection!r} "
            f"not in available connectors {sorted(connectors)}"
        )
    # Stream fan-out / conditional routing are out of scope for v1 (ADR-0026/27):
    # a stream sink commits offsets, so multi-sink / per-record routing across
    # stream sinks needs a commit policy we haven't designed.
    if mode == "stream" and len(sink_cfgs) > 1:
        raise ConfigError(f"{label}: fan-out to multiple sinks is not supported in stream mode")
    if mode == "stream" and any(s.when is not None for s in sink_cfgs):
        raise ConfigError(
            f"{label}: conditional sink routing ('when') is not supported in stream mode"
        )
    for snk in sink_cfgs:
        if snk.connection not in connectors:
            raise ConfigError(
                f"{label}: sink connection {snk.connection!r} "
                f"not in available connectors {sorted(connectors)}"
            )
        if snk.when is not None:
            try:
                compile(snk.when, "<sink:when>", "eval")
            except SyntaxError as exc:
                raise ConfigError(
                    f"{label}: sink {snk.connection!r} has invalid routing 'when': {exc}"
                ) from exc

    # Branch rules (ADR-0028): validate each predicate compiles at build time.
    for br in task_cfg.branch:
        if br.when is not None:
            try:
                compile(br.when, "<branch:when>", "eval")
            except SyntaxError as exc:
                raise ConfigError(f"{label}: invalid branch 'when': {exc}") from exc

    task = Task(
        name=task_cfg.name,
        source=src.connection,
        query=src.query,
        cursor_column=src.cursor_column,
        source_options=src.model_dump(exclude={"connection", "query", "cursor_column"}),
        depends_on=list(task_cfg.depends_on),
        trigger_rule=task_cfg.trigger_rule,
        branch=[BranchRule(when=br.when, to=list(br.to)) for br in task_cfg.branch],
        retry=task_cfg.retry,
        timeout_seconds=task_cfg.timeout_seconds,
        expand=dict(task_cfg.expand),
        push_xcom={k: dict(v) for k, v in task_cfg.push_xcom.items()},
    )
    # When a sink reuses the source's connection, give it a dedicated instance
    # (separate physical connection) so the streaming read cursor and the write
    # don't deadlock on a single shared connection.
    source_names = {src.connection}

    def _sink_key(conn_name: str) -> str:
        return _dedicated_sink_key(
            conn_name,
            source_names=source_names,
            connectors=connectors,
            factory=connector_factory,
        )

    # Collapse to the flat single-sink fields only when there's exactly one
    # sink with no routing predicate (that path can't carry ``when``).
    if len(sink_cfgs) == 1 and sink_cfgs[0].when is None:
        snk = sink_cfgs[0]
        task.sink = _sink_key(snk.connection)
        # ADR-0094 f/u: keep the ORIGINAL connection name — ``task.sink``
        # may be a minted dedicated-instance key (``db__sink``) and
        # same-connection pushdown compares databases by name.
        task.sink_connection_name = snk.connection
        task.sink_table = snk.table
        task.sink_mode = snk.mode
        task.sink_key_columns = snk.key_columns
        task.sink_options = snk.model_dump(exclude=_SINK_OPTS_EXCLUDE)
        task.sink_pre_sql = snk.model_dump().get("pre_sql")
        task.sink_auto_create_table = bool(snk.model_dump().get("auto_create_table"))
        task.sink_auto_create_if_exists = str(
            snk.model_dump().get("auto_create_if_exists") or "skip"
        )
    else:
        task.sinks = [
            SinkSpec(
                name=_sink_key(snk.connection),
                table=snk.table,
                mode=snk.mode,
                key_columns=snk.key_columns,
                options=snk.model_dump(exclude=_SINK_OPTS_EXCLUDE),
                when=snk.when,
                pre_sql=snk.model_dump().get("pre_sql"),
                auto_create_table=bool(snk.model_dump().get("auto_create_table")),
                auto_create_if_exists=str(snk.model_dump().get("auto_create_if_exists") or "skip"),
                connection_name=snk.connection,
            )
            for snk in sink_cfgs
        ]
    for tc in task_cfg.transforms:
        # "sql_exec" is not a per-record transform — it's a pre-load action
        # (ADR-0035) that runs a statement once before reading (delete-then-
        # insert idempotency). Pull it out of the transform chain.
        if tc.type == "sql_exec":
            data = tc.model_dump()
            conn_name = data.get("connection")
            statement = data.get("statement")
            if not conn_name or not statement:
                raise ConfigError(f"{label}: sql_exec step requires 'connection' and 'statement'")
            if conn_name not in connectors:
                raise ConfigError(
                    f"{label}: sql_exec connection {conn_name!r} "
                    f"not in available connectors {sorted(connectors)}"
                )
            task.pre_sql.append(SqlAction(connection=conn_name, statement=statement))
        else:
            task.transform(build_transform(tc))
            # Phase XX (ADR-0068): keep the raw spec alongside the
            # compiled callable so ``_auto_create_sink_tables`` can
            # project source columns through the declarative chain.
            task.transform_specs.append(tc.model_dump())
    return task


_GRAPH_NODE_EXCLUDE = {
    "id",
    "type",
    "connection",
    "query",
    "table",
    "mode",
    "key_columns",
    "transform",
}


def _build_graph_task(
    graph: GraphConfig,
    *,
    pipeline_name: str,
    connectors: dict[str, Connector],
    connector_factory: Callable[[str], Connector] | None = None,
) -> Task:
    """Build a dataflow-graph :class:`Task` from a :class:`GraphConfig`.

    ADR-0030 single-source trees and ADR-0041 free DAGs (multi-source + ``join``
    fan-in) both build here; the materialize engine (``Pipeline._run_graph_task``)
    executes them topologically.
    """
    label = f"pipeline {pipeline_name!r} graph"
    # Sinks that reuse a source connection get a dedicated instance so the
    # streaming read + write don't deadlock on one shared connection.
    source_names = {n.connection for n in graph.nodes if n.type == "source" and n.connection}
    nodes: list[GraphNode] = []
    for n in graph.nodes:
        if n.type == "source":
            if not n.connection or n.connection not in connectors:
                raise ConfigError(
                    f"{label}: node {n.id!r} source connection {n.connection!r} unavailable"
                )
            nodes.append(
                GraphNode(
                    id=n.id,
                    kind="source",
                    source_name=n.connection,
                    query=n.query,
                    source_options=n.model_dump(exclude=_GRAPH_NODE_EXCLUDE),
                )
            )
        elif n.type == "transform":
            if n.transform is None:
                raise ConfigError(f"{label}: transform node {n.id!r} missing 'transform'")
            nodes.append(
                GraphNode(
                    id=n.id,
                    kind="transform",
                    transform_fn=build_transform(n.transform),
                    transform_spec=n.transform.model_dump(),
                )
            )
        elif n.type == "sink":
            if not n.connection or n.connection not in connectors:
                raise ConfigError(
                    f"{label}: node {n.id!r} sink connection {n.connection!r} unavailable"
                )
            sink_key = _dedicated_sink_key(
                n.connection,
                source_names=source_names,
                connectors=connectors,
                factory=connector_factory,
            )
            nodes.append(
                GraphNode(
                    id=n.id,
                    kind="sink",
                    sink=SinkSpec(
                        name=sink_key,
                        table=n.table,
                        mode=n.mode,
                        key_columns=n.key_columns,
                        options=n.model_dump(exclude=_GRAPH_NODE_EXCLUDE),
                        auto_create_table=bool(n.model_dump().get("auto_create_table")),
                        auto_create_if_exists=str(
                            n.model_dump().get("auto_create_if_exists") or "skip"
                        ),
                        connection_name=n.connection,
                    ),
                )
            )
        elif n.type == "join":
            nodes.append(GraphNode(id=n.id, kind="join", join_on=n.on, join_how=n.how))
        elif n.type == "aggregate":
            nodes.append(
                GraphNode(
                    id=n.id,
                    kind="aggregate",
                    agg_group_by=n.group_by,
                    aggregations=[
                        AggSpec(op=a.op, name=a.name, column=a.column)
                        for a in (n.aggregations or [])
                    ],
                )
            )
        elif n.type == "sql_exec":
            # ADR-0042 follow-up — standalone SQL-execution node. Same
            # connector-resolution semantics as a source so the user
            # can target any DB connection registered in the workspace.
            if not n.connection or n.connection not in connectors:
                raise ConfigError(
                    f"{label}: node {n.id!r} sql_exec connection {n.connection!r} unavailable"
                )
            if not n.statement:
                raise ConfigError(f"{label}: sql_exec node {n.id!r} missing 'statement'")
            nodes.append(
                GraphNode(
                    id=n.id,
                    kind="sql_exec",
                    source_name=n.connection,
                    sql_statement=n.statement,
                )
            )
        else:
            raise ConfigError(f"{label}: node {n.id!r} has unknown type {n.type!r}")

    edges: list[GraphEdge] = []
    for e in graph.edges:
        if e.when is not None:
            try:
                compile(e.when, "<edge:when>", "eval")
            except SyntaxError as exc:
                raise ConfigError(
                    f"{label}: edge {e.from_node}→{e.to_node} invalid 'when': {exc}"
                ) from exc
        edges.append(GraphEdge(from_id=e.from_node, to_id=e.to_node, when=e.when))

    return Task(name=pipeline_name, graph_nodes=nodes, graph_edges=edges)


def build_pipeline(
    pipeline_config: PipelineConfig,
    connectors: dict[str, Connector] | None = None,
    *,
    connector_factory: Callable[[str], Connector] | None = None,
) -> tuple[Pipeline, dict[str, Connector]]:
    """Build a Pipeline from a PipelineConfig.

    Handles the single-task shape, a Task-orchestration DAG (``tasks`` +
    ``depends_on``, ADR-0028), and a dataflow graph (``graph``, ADR-0030).
    ``connectors`` is the available set; missing connections raise
    :class:`ConfigError`.

    ``connector_factory`` (optional) mints a fresh connector instance for a
    given connection name. When provided, any sink that reuses a source's
    connection is given its own instance (a separate physical connection) so
    the streaming read + write don't deadlock on one shared connection. The
    minted instances are added to the returned ``connectors`` dict so the
    caller connects/closes them like the rest.
    """
    if connectors is None:
        connectors = {}

    # Dataflow graph shape (ADR-0030) — its own builder + executor.
    if pipeline_config.graph is not None:
        if pipeline_config.mode == "stream":
            raise ConfigError(f"pipeline {pipeline_config.name!r}: dataflow graphs are batch-only")
        graph_task = _build_graph_task(
            pipeline_config.graph,
            pipeline_name=pipeline_config.name,
            connectors=connectors,
            connector_factory=connector_factory,
        )
        commit_strategy = (
            pipeline_config.commit.strategy if pipeline_config.commit else "after_sink_flush"
        )
        pipeline = Pipeline(
            name=pipeline_config.name,
            mode=pipeline_config.mode,
            commit_strategy=commit_strategy,
            retry=pipeline_config.retry,
            task_timeout_seconds=pipeline_config.task_timeout_seconds,
            dlq=pipeline_config.dlq,
        )
        pipeline.add(graph_task)
        _attach_column_lineage(pipeline, pipeline_config)
        return pipeline, connectors

    task_cfgs = pipeline_config.effective_tasks()
    # Stream DAGs (multiple tasks) are out of scope for v1 — keep stream
    # pipelines single-task. Topological execution is batch-only for now.
    if pipeline_config.mode == "stream" and len(task_cfgs) > 1:
        raise ConfigError(
            f"pipeline {pipeline_config.name!r}: multi-task DAGs are not supported in stream mode"
        )

    tasks = [
        _build_task(
            tc,
            pipeline_name=pipeline_config.name,
            mode=pipeline_config.mode,
            connectors=connectors,
            connector_factory=connector_factory,
        )
        for tc in task_cfgs
    ]
    # Validate depends_on references resolve within this pipeline (clearer error
    # here than at run time).
    names = {t.name for t in tasks}
    for tc in task_cfgs:
        for dep in tc.depends_on:
            if dep not in names:
                raise ConfigError(
                    f"pipeline {pipeline_config.name!r}: task {tc.name!r} "
                    f"depends on unknown task {dep!r}"
                )
    # Branch targets must be direct downstreams (a task that depends_on the
    # branch task) — otherwise the skip/select semantics are undefined.
    for tc in task_cfgs:
        if not tc.branch:
            continue
        direct = {t.name for t in task_cfgs if tc.name in t.depends_on}
        for br in tc.branch:
            for target in br.to:
                if target not in direct:
                    raise ConfigError(
                        f"pipeline {pipeline_config.name!r}: branch task {tc.name!r} "
                        f"targets {target!r}, which is not a direct downstream "
                        f"(a task with depends_on: [{tc.name!r}])"
                    )

    commit_strategy = (
        pipeline_config.commit.strategy if pipeline_config.commit else "after_sink_flush"
    )
    if pipeline_config.dlq is not None and pipeline_config.dlq.connection not in connectors:
        raise ConfigError(
            f"pipeline {pipeline_config.name!r}: dlq connection "
            f"{pipeline_config.dlq.connection!r} not in available connectors {sorted(connectors)}"
        )
    pipeline = Pipeline(
        name=pipeline_config.name,
        mode=pipeline_config.mode,
        commit_strategy=commit_strategy,
        retry=pipeline_config.retry,
        task_timeout_seconds=pipeline_config.task_timeout_seconds,
        dlq=pipeline_config.dlq,
    )
    for task in tasks:
        pipeline.add(task)
    # Surface cycles / duplicate names now (dry-run) rather than at run time.
    pipeline._ordered_tasks()
    _attach_column_lineage(pipeline, pipeline_config)
    return pipeline, connectors


def _attach_column_lineage(pipeline: Pipeline, cfg: PipelineConfig) -> None:
    """Compute static column lineage from ``cfg`` and stash on the Pipeline
    so emitters (e.g. OpenLineage, ADR-0041 K5b) can attach a
    ``columnLineage`` facet to output datasets without re-deriving.

    Best-effort — any derivation failure (unsupported transform shape,
    unparseable SQL, …) leaves ``column_lineage`` unset and the run still
    builds. Table-level lineage emission stays unaffected.
    """
    try:
        pipeline.column_lineage = derive_column_lineage(cfg)
    except Exception:
        pipeline.column_lineage = None


def build_pipeline_from_yaml(
    pipeline_path: str | Path,
    *,
    connections_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    secret_backend: SecretBackend | None = None,
    extra_connectors: dict[str, Connector] | None = None,
    runtime_context: RuntimeContext | None = None,
) -> tuple[Pipeline, dict[str, Connector]]:
    """Load YAML config and instantiate the Pipeline + its connectors.

    Parameters
    ----------
    pipeline_path
        Path to ``configs/pipelines/<x>.yaml``.
    connections_path
        Path to ``configs/connections.yaml``. If None, no connections are
        loaded — callers must provide ``extra_connectors``.
    env, secret_backend
        Forwarded to the YAML loader for ``${VAR}`` and ``!secret`` resolution.
    extra_connectors
        Pre-instantiated connectors (e.g. mocks for tests). Merged on top of
        whatever ``connections_path`` produces.
    """
    pc = load_pipeline(pipeline_path, env=env, secret_backend=secret_backend)
    # Runtime templating (자유도 1단계): render ``{{ ds }}`` / ``{{ params.x }}``
    # AFTER static ${var}/secret/env resolution (load_pipeline) and BEFORE
    # build, so the pipeline carries concrete per-run values. Trigger-time
    # params override the config's declared ``params`` defaults; the
    # pipeline's name is injected so ``{{ pipeline_name }}`` works.
    if runtime_context is not None:
        ctx = replace(
            runtime_context,
            params={**pc.params, **runtime_context.params},
            pipeline_name=runtime_context.pipeline_name or pc.name,
        )
        rendered = render_config_templates(pc.model_dump(), ctx)
        pc = PipelineConfig.model_validate(rendered)
    connectors: dict[str, Connector] = {}
    conn_configs: dict[str, ConnectionConfig] = {}
    if connections_path is not None:
        cc = load_connections(connections_path, env=env, secret_backend=secret_backend)
        conn_configs = dict(cc.connections)
        connectors.update(build_connectors(cc))
    if extra_connectors:
        connectors.update(extra_connectors)

    # Re-instantiate from config so a sink reusing a source connection gets its
    # own connection (avoids the single-connection read+write deadlock).
    def _factory(name: str) -> Connector:
        if name not in conn_configs:
            raise ConfigError(
                f"cannot create a dedicated sink connection for {name!r}: "
                "no connection config available (pre-built connector only)"
            )
        return build_connector(name, conn_configs[name])

    return build_pipeline(pc, connectors, connector_factory=_factory)


__all__ = [
    "build_connector",
    "build_connectors",
    "build_pipeline",
    "build_pipeline_from_yaml",
]


# Type-only re-export to silence unused-import lint in some IDEs without changing behaviour
_PipelineAlias = Pipeline
_AnyAlias = Any
