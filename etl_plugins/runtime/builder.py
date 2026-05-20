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

from collections.abc import Mapping
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
    BranchRule,
    GraphEdge,
    GraphNode,
    Pipeline,
    SinkSpec,
    Task,
)
from etl_plugins.core.registry import ConnectorRegistry
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


_SINK_OPTS_EXCLUDE = {"connection", "table", "mode", "key_columns", "when"}


def _build_task(
    task_cfg: TaskConfig,
    *,
    pipeline_name: str,
    mode: str,
    connectors: dict[str, Connector],
) -> Task:
    """Build one runtime :class:`Task` from a :class:`TaskConfig`."""
    src = task_cfg.source
    sink_cfgs = task_cfg.effective_sinks()
    label = f"pipeline {pipeline_name!r} task {task_cfg.name!r}"

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
        source_options=src.model_dump(exclude={"connection", "query"}),
        depends_on=list(task_cfg.depends_on),
        trigger_rule=task_cfg.trigger_rule,
        branch=[BranchRule(when=br.when, to=list(br.to)) for br in task_cfg.branch],
    )
    # Collapse to the flat single-sink fields only when there's exactly one
    # sink with no routing predicate (that path can't carry ``when``).
    if len(sink_cfgs) == 1 and sink_cfgs[0].when is None:
        snk = sink_cfgs[0]
        task.sink = snk.connection
        task.sink_table = snk.table
        task.sink_mode = snk.mode
        task.sink_key_columns = snk.key_columns
        task.sink_options = snk.model_dump(exclude=_SINK_OPTS_EXCLUDE)
    else:
        task.sinks = [
            SinkSpec(
                name=snk.connection,
                table=snk.table,
                mode=snk.mode,
                key_columns=snk.key_columns,
                options=snk.model_dump(exclude=_SINK_OPTS_EXCLUDE),
                when=snk.when,
            )
            for snk in sink_cfgs
        ]
    for tc in task_cfg.transforms:
        task.transform(build_transform(tc))
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
    graph: GraphConfig, *, pipeline_name: str, connectors: dict[str, Connector]
) -> Task:
    """Build a dataflow-graph :class:`Task` from a :class:`GraphConfig` (ADR-0030)."""
    label = f"pipeline {pipeline_name!r} graph"
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
                GraphNode(id=n.id, kind="transform", transform_fn=build_transform(n.transform))
            )
        elif n.type == "sink":
            if not n.connection or n.connection not in connectors:
                raise ConfigError(
                    f"{label}: node {n.id!r} sink connection {n.connection!r} unavailable"
                )
            nodes.append(
                GraphNode(
                    id=n.id,
                    kind="sink",
                    sink=SinkSpec(
                        name=n.connection,
                        table=n.table,
                        mode=n.mode,
                        key_columns=n.key_columns,
                        options=n.model_dump(exclude=_GRAPH_NODE_EXCLUDE),
                    ),
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
) -> tuple[Pipeline, dict[str, Connector]]:
    """Build a Pipeline from a PipelineConfig.

    Handles the single-task shape, a Task-orchestration DAG (``tasks`` +
    ``depends_on``, ADR-0028), and a dataflow graph (``graph``, ADR-0030).
    ``connectors`` is the available set; missing connections raise
    :class:`ConfigError`.
    """
    if connectors is None:
        connectors = {}

    # Dataflow graph shape (ADR-0030) — its own builder + executor.
    if pipeline_config.graph is not None:
        if pipeline_config.mode == "stream":
            raise ConfigError(f"pipeline {pipeline_config.name!r}: dataflow graphs are batch-only")
        graph_task = _build_graph_task(
            pipeline_config.graph, pipeline_name=pipeline_config.name, connectors=connectors
        )
        commit_strategy = (
            pipeline_config.commit.strategy if pipeline_config.commit else "after_sink_flush"
        )
        pipeline = Pipeline(
            name=pipeline_config.name,
            mode=pipeline_config.mode,
            commit_strategy=commit_strategy,
            retry=pipeline_config.retry,
            dlq=pipeline_config.dlq,
        )
        pipeline.add(graph_task)
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
        dlq=pipeline_config.dlq,
    )
    for task in tasks:
        pipeline.add(task)
    # Surface cycles / duplicate names now (dry-run) rather than at run time.
    pipeline._ordered_tasks()
    return pipeline, connectors


def build_pipeline_from_yaml(
    pipeline_path: str | Path,
    *,
    connections_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    secret_backend: SecretBackend | None = None,
    extra_connectors: dict[str, Connector] | None = None,
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
    connectors: dict[str, Connector] = {}
    if connections_path is not None:
        cc = load_connections(connections_path, env=env, secret_backend=secret_backend)
        connectors.update(build_connectors(cc))
    if extra_connectors:
        connectors.update(extra_connectors)
    return build_pipeline(pc, connectors)


__all__ = [
    "build_connector",
    "build_connectors",
    "build_pipeline",
    "build_pipeline_from_yaml",
]


# Type-only re-export to silence unused-import lint in some IDEs without changing behaviour
_PipelineAlias = Pipeline
_AnyAlias = Any
