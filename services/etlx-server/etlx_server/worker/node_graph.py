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

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from structlog.contextvars import bind_contextvars, unbind_contextvars

from etl_plugins.config.models import ConnectionConfig
from etl_plugins.core.connector import Connector
from etl_plugins.core.exceptions import TaskError
from etl_plugins.core.pipeline import (
    GraphEdge,
    GraphNode,
    Task,
    apply_edge_predicate,
    execute_graph_node,
)
from etl_plugins.core.record import Record
from etl_plugins.runtime.builder import build_connector

OnNodeStart = Callable[[str], Awaitable[None]]
OnNodeFinish = Callable[["NodeOutcome"], Awaitable[None]]

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


def _connection_names_for(node: GraphNode) -> list[str]:
    """The connection name(s) a node needs at execution time (source/sink only)."""
    if node.kind == "source" and node.source_name:
        return [node.source_name]
    if node.kind == "sink" and node.sink:
        return [node.sink.name]
    return []


async def execute_graph_nodes_concurrent(
    task: Task,
    conn_cfgs: dict[str, ConnectionConfig],
    *,
    on_node_start: OnNodeStart | None = None,
    on_node_finish: OnNodeFinish | None = None,
) -> list[NodeOutcome]:
    """Run ``task``'s graph wave-by-wave with per-node connectors + concurrency
    (ADR-0041, H2c — the real parallelism path).

    Each node runs in its own ``asyncio.to_thread``, where it **mints + connects
    + closes its own connector instance(s)** (thread-bound drivers like sqlite/
    psycopg can't be safely shared across threads). Within a wave, independent
    ready nodes run concurrently via :func:`asyncio.gather`. A node whose
    upstream failed/skipped is itself skipped.

    Optional ``on_node_start`` / ``on_node_finish`` callbacks fire around each
    node (H3a live progress). They run in the event loop between waves —
    safe to do async DB writes that commit per-node so a UI poll sees progress
    mid-run rather than only after the whole run finishes.

    Returns per-node outcomes in topological order (same shape as
    :func:`execute_graph_nodes`). The caller persists them as ``node_runs``.
    """
    by_id = {n.id: n for n in task.graph_nodes}
    incoming: dict[str, list[GraphEdge]] = {n.id: [] for n in task.graph_nodes}
    downstream: dict[str, list[str]] = {n.id: [] for n in task.graph_nodes}
    for e in task.graph_edges:
        incoming[e.to_id].append(e)
        downstream[e.from_id].append(e.to_id)
    pending_deps = {nid: len(incoming[nid]) for nid in by_id}
    outputs: dict[str, list[Record]] = {}
    outcomes: dict[str, NodeOutcome] = {}
    blocked: set[str] = set()  # failed or skipped — descendants skip
    remaining = set(by_id)

    def _run_node_in_thread(node: GraphNode, inputs: list[list[Record]]) -> object:
        """Mint per-node connector(s) in this thread, execute, close — never
        shares connections across threads (thread-bound driver safety).

        Binds ``node_id`` into structlog's contextvars (Phase M,
        2026-05-26) so every log line emitted between bind and unbind —
        whether from connector setup, the operator itself, or its
        teardown — automatically carries the node id. The recorder's
        ``log_processor`` lifts it into a first-class ``run_logs.node_id``
        column on persist; the run-detail UI uses that to filter the
        log panel to one node when the operator clicks a card in the
        run DAG. ``to_thread`` propagates the caller's contextvars
        context into the worker thread, so binding here is enough — we
        don't need to re-bind on every connector call.
        """
        local: dict[str, Connector] = {}
        bind_contextvars(node_id=node.id)
        try:
            for name in _connection_names_for(node):
                if name not in conn_cfgs:
                    raise TaskError(
                        f"node {node.id!r}: connection {name!r} not in resolved configs"
                    )
                connector = build_connector(name, conn_cfgs[name])
                connector.connect()
                local[name] = connector
            return execute_graph_node(node, inputs, local)
        finally:
            for connector in local.values():
                with contextlib.suppress(Exception):
                    connector.close()
            unbind_contextvars("node_id")

    async def _run_one(nid: str) -> None:
        node = by_id[nid]
        if on_node_start is not None:
            await on_node_start(nid)
        inputs = [apply_edge_predicate(outputs[e.from_id], e.when) for e in incoming[nid]]
        try:
            result = await asyncio.to_thread(_run_node_in_thread, node, inputs)
        except Exception as exc:  # record per-node failure, don't abort the wave
            outcomes[nid] = NodeOutcome(
                nid,
                node.kind,
                NODE_FAILED,
                error_class=type(exc).__name__,
                error_message=str(exc),
            )
            blocked.add(nid)
            if on_node_finish is not None:
                await on_node_finish(outcomes[nid])
            return
        outputs[nid] = result.output  # type: ignore[attr-defined]
        outcomes[nid] = NodeOutcome(
            nid,
            node.kind,
            NODE_SUCCEEDED,
            records_read=result.records_read,  # type: ignore[attr-defined]
            records_written=result.records_written,  # type: ignore[attr-defined]
        )
        if on_node_finish is not None:
            await on_node_finish(outcomes[nid])

    while remaining:
        # Mark skipped any remaining node whose upstream is blocked, so they
        # don't get claimed when pending_deps hits 0 (their dep "completed"
        # but blocked, not succeeded).
        for nid in list(remaining):
            if any(e.from_id in blocked for e in incoming[nid]):
                outcomes[nid] = NodeOutcome(nid, by_id[nid].kind, NODE_SKIPPED)
                blocked.add(nid)
                remaining.discard(nid)
                if on_node_finish is not None:
                    await on_node_finish(outcomes[nid])

        ready = [nid for nid in remaining if pending_deps[nid] == 0]
        if not ready:
            break  # remaining (if any) all blocked indirectly — loop exits

        # Run all ready nodes concurrently — each in its own thread + own
        # connector instances. asyncio.gather waits for the whole wave.
        await asyncio.gather(*[_run_one(nid) for nid in ready])

        for nid in ready:
            remaining.discard(nid)
            for dn in downstream[nid]:
                pending_deps[dn] = max(0, pending_deps[dn] - 1)

    order = _topo_order(task)
    return [outcomes[nid] for nid in order if nid in outcomes]


__all__ = [
    "NODE_FAILED",
    "NODE_SKIPPED",
    "NODE_SUCCEEDED",
    "NodeOutcome",
    "execute_graph_nodes",
    "execute_graph_nodes_concurrent",
]
