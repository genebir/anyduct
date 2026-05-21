"""Lower any pipeline shape into the unified dataflow graph (ADR-0041, G1).

A :class:`PipelineConfig` has three historical shapes — single-task (linear),
Task-DAG (ADR-0028), and dataflow graph (ADR-0030). ADR-0041 makes the dataflow
:class:`GraphConfig` the *one* model: every operator is a node, records flow
along edges, and fan-in happens at explicit ``join`` nodes.

:func:`to_graph` produces that canonical form. It is a pure, derivation-only
function (no execution) — the materialize engine (G2) and lineage build on it.
Lowering does **not** change how a pipeline executes today: a single-task
pipeline keeps its fast linear path unless something opts into the graph form.

Task-DAG lowering is deferred: ``depends_on`` / trigger rules are *control*
flow, which is reconciled when node-level scheduling lands (ADR-0041, Phase H).
"""

from __future__ import annotations

from etl_plugins.config.models import (
    GraphConfig,
    GraphEdgeConfig,
    GraphNodeConfig,
    PipelineConfig,
)

__all__ = ["node_dependencies", "to_graph", "topological_order"]

_SOURCE_ID = "source"


def to_graph(cfg: PipelineConfig) -> GraphConfig:
    """Return the unified :class:`GraphConfig` for any pipeline shape.

    * **graph** shape → returned as-is (already canonical).
    * **single-task** shape → lowered to ``source → transform* → sink+``
      (sink ``when`` routing becomes the edge predicate, ADR-0027).
    * **Task-DAG** shape → :class:`NotImplementedError` (Phase H).

    The returned graph is re-validated by :class:`GraphConfig`'s own validator.
    """
    if cfg.graph is not None:
        return cfg.graph
    if cfg.tasks:
        raise NotImplementedError(
            "Task-DAG → graph lowering lands with node-level scheduling (ADR-0041, Phase H)"
        )
    if cfg.source is None:  # pragma: no cover - guarded by PipelineConfig._check_shape
        raise ValueError("pipeline has no source to lower into a graph")

    nodes: list[GraphNodeConfig] = []
    edges: list[GraphEdgeConfig] = []

    # Source node — connection/query are typed fields; cursor_column, chunk_size,
    # topic, format, … ride along as extras (GraphNodeConfig allows extra).
    nodes.append(GraphNodeConfig(id=_SOURCE_ID, type="source", **cfg.source.model_dump()))

    # Transform chain — linear spine off the source.
    tail = _SOURCE_ID
    for i, tc in enumerate(cfg.transforms):
        node_id = f"transform_{i}"
        nodes.append(GraphNodeConfig(id=node_id, type="transform", transform=tc))
        edges.append(GraphEdgeConfig(from_node=tail, to_node=node_id))
        tail = node_id

    # Sinks fan out from the tail; a sink's ``when`` (ADR-0027 routing) becomes
    # the edge predicate so the unified model carries one routing concept.
    for i, sink in enumerate(cfg.effective_sinks()):
        node_id = f"sink_{i}"
        nodes.append(GraphNodeConfig(id=node_id, type="sink", **sink.model_dump(exclude={"when"})))
        edges.append(GraphEdgeConfig(from_node=tail, to_node=node_id, when=sink.when))

    return GraphConfig(nodes=nodes, edges=edges)


def node_dependencies(graph: GraphConfig) -> dict[str, list[str]]:
    """Map each node id → its direct upstream node ids (incoming-edge sources).

    Used to expand a graph into ``node_runs`` with ``depends_on`` for node-level
    scheduling (ADR-0041, H2).
    """
    deps: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for e in graph.edges:
        deps[e.to_node].append(e.from_node)
    return deps


def topological_order(graph: GraphConfig) -> list[str]:
    """Node ids in a topological order (Kahn). Assumes ``graph`` is acyclic.

    The :class:`GraphConfig` validator already rejects cycles, so a caller that
    passes a validated graph always gets a complete ordering.
    """
    indegree: dict[str, int] = {n.id: 0 for n in graph.nodes}
    adjacency: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for e in graph.edges:
        indegree[e.to_node] += 1
        adjacency[e.from_node].append(e.to_node)

    # Stable: seed + extend in node declaration order.
    order: list[str] = []
    ready = [n.id for n in graph.nodes if indegree[n.id] == 0]
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for nxt in adjacency[cur]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
    if len(order) != len(graph.nodes):  # pragma: no cover - validator rejects cycles
        raise ValueError("graph has a cycle")
    return order
