"""Node-by-node execution of a graph Task (ADR-0041, H2b).

When a graph pipeline opts into ``node_level``, the worker runs it one node at a
time (instead of one whole-graph pass) so each node gets its own ``node_run``
row — status, counters, and the foundation for per-node retry + the DAG progress
view (H3).

H2b runs nodes **sequentially in one thread** (thread-safe with the shared,
once-connected connectors). Intra-run parallelism — independent nodes running
concurrently — needs per-node connector instances (thread-bound drivers can't be
shared across threads) and lands in H2c. The orchestration here reuses the core
per-node operator (:func:`execute_graph_node`) + edge filter
(:func:`apply_edge_predicate`) extracted in H2a, so H2c only swaps the runner.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from etl_plugins.core.connector import Connector
from etl_plugins.core.pipeline import GraphEdge, Task, apply_edge_predicate, execute_graph_node
from etl_plugins.core.record import Record

NODE_SUCCEEDED = "succeeded"
NODE_FAILED = "failed"
NODE_SKIPPED = "skipped"


@dataclass
class NodeOutcome:
    """Per-node result of a node-level graph run."""

    node_id: str
    kind: str
    status: str  # succeeded | failed | skipped
    records_read: int = 0
    records_written: int = 0
    error_class: str | None = None
    error_message: str | None = None


def _topo_order(task: Task) -> list[str]:
    indeg = {n.id: 0 for n in task.graph_nodes}
    adjacency: dict[str, list[str]] = {n.id: [] for n in task.graph_nodes}
    for e in task.graph_edges:
        indeg[e.to_id] += 1
        adjacency[e.from_id].append(e.to_id)
    ready = [nid for nid, d in indeg.items() if d == 0]
    order: list[str] = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for nxt in adjacency[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
    return order


def execute_graph_nodes(task: Task, connectors: dict[str, Connector]) -> list[NodeOutcome]:
    """Run ``task``'s graph node-by-node in one thread; return per-node outcomes.

    Outputs are held in memory and passed along edges (filtered by each edge's
    ``when``). A node whose upstream failed/skipped is itself skipped (not run),
    so the failure propagates downstream. Connectors are connected once up front
    and closed in a ``finally`` — call this inside a single worker thread.
    """
    by_id = {n.id: n for n in task.graph_nodes}
    incoming: dict[str, list[GraphEdge]] = {n.id: [] for n in task.graph_nodes}
    for e in task.graph_edges:
        incoming[e.to_id].append(e)

    order = _topo_order(task)
    outputs: dict[str, list[Record]] = {}
    outcomes: dict[str, NodeOutcome] = {}
    blocked: set[str] = set()  # failed or skipped — descendants skip

    for connector in connectors.values():
        connector.connect()
    try:
        for node_id in order:
            node = by_id[node_id]
            if any(e.from_id in blocked for e in incoming[node_id]):
                outcomes[node_id] = NodeOutcome(node_id, node.kind, NODE_SKIPPED)
                blocked.add(node_id)
                continue
            inputs = [apply_edge_predicate(outputs[e.from_id], e.when) for e in incoming[node_id]]
            try:
                result = execute_graph_node(node, inputs, connectors)
            except Exception as exc:
                outcomes[node_id] = NodeOutcome(
                    node_id,
                    node.kind,
                    NODE_FAILED,
                    error_class=type(exc).__name__,
                    error_message=str(exc),
                )
                blocked.add(node_id)
                continue
            outputs[node_id] = result.output
            outcomes[node_id] = NodeOutcome(
                node_id,
                node.kind,
                NODE_SUCCEEDED,
                records_read=result.records_read,
                records_written=result.records_written,
            )
    finally:
        for connector in connectors.values():
            with contextlib.suppress(Exception):
                connector.close()

    return [outcomes[node_id] for node_id in order]


__all__ = ["NODE_FAILED", "NODE_SKIPPED", "NODE_SUCCEEDED", "NodeOutcome", "execute_graph_nodes"]
