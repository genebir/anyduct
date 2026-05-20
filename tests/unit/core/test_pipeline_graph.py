"""Dataflow graph execution (ADR-0030) — branching at any node, free order."""

from __future__ import annotations

import pytest

from etl_plugins.config.models import GraphConfig, PipelineConfig
from etl_plugins.core.exceptions import TaskError
from etl_plugins.core.pipeline import GraphEdge, GraphNode, Pipeline, SinkSpec, Task
from etl_plugins.core.record import Record

from .conftest import InMemoryBatchSink, InMemoryBatchSource


def _src(*types: str) -> InMemoryBatchSource:
    return InMemoryBatchSource([Record(data={"type": t, "n": i}) for i, t in enumerate(types)])


def _graph_task(nodes: list[GraphNode], edges: list[GraphEdge]) -> Task:
    return Task(name="g", graph_nodes=nodes, graph_edges=edges)


def test_graph_linear_source_transform_sink() -> None:
    def tag(r: Record) -> Record:
        return Record(data={**r.data, "tagged": True}, metadata=r.metadata)

    nodes = [
        GraphNode(id="s", kind="source", source_name="src"),
        GraphNode(id="t", kind="transform", transform_fn=tag),
        GraphNode(id="k", kind="sink", sink=SinkSpec(name="snk", table="o")),
    ]
    edges = [GraphEdge("s", "t"), GraphEdge("t", "k")]
    snk = InMemoryBatchSink()
    result = (
        Pipeline("g")
        .add(_graph_task(nodes, edges))
        .run(connectors={"src": _src("a", "b"), "snk": snk})
    )
    assert result.success and result.records_read == 2 and result.records_written == 2
    assert all(r.data["tagged"] for r in snk.records)


def test_graph_branch_routes_records_by_edge_when() -> None:
    # source → {sink_a (type==a), sink_b (type==b)} — branch at the source node.
    nodes = [
        GraphNode(id="s", kind="source", source_name="src"),
        GraphNode(id="ka", kind="sink", sink=SinkSpec(name="a", table="o")),
        GraphNode(id="kb", kind="sink", sink=SinkSpec(name="b", table="o")),
    ]
    edges = [
        GraphEdge("s", "ka", when="data['type'] == 'a'"),
        GraphEdge("s", "kb", when="data['type'] == 'b'"),
    ]
    sa, sb = InMemoryBatchSink(), InMemoryBatchSink()
    result = (
        Pipeline("g")
        .add(_graph_task(nodes, edges))
        .run(connectors={"src": _src("a", "b", "a", "c"), "a": sa, "b": sb})
    )
    assert result.records_read == 4
    assert [r.data["type"] for r in sa.records] == ["a", "a"]
    assert [r.data["type"] for r in sb.records] == ["b"]


def test_graph_branch_mid_pipeline_after_transform() -> None:
    # source → t → {sink_hi (n>=1), sink_lo (n<1)} — branch after a transform.
    def keep(r: Record) -> Record:
        return r

    nodes = [
        GraphNode(id="s", kind="source", source_name="src"),
        GraphNode(id="t", kind="transform", transform_fn=keep),
        GraphNode(id="hi", kind="sink", sink=SinkSpec(name="hi", table="o")),
        GraphNode(id="lo", kind="sink", sink=SinkSpec(name="lo", table="o")),
    ]
    edges = [
        GraphEdge("s", "t"),
        GraphEdge("t", "hi", when="data['n'] >= 1"),
        GraphEdge("t", "lo", when="data['n'] < 1"),
    ]
    hi, lo = InMemoryBatchSink(), InMemoryBatchSink()
    Pipeline("g").add(_graph_task(nodes, edges)).run(
        connectors={"src": _src("a", "b", "c"), "hi": hi, "lo": lo}
    )
    assert [r.data["n"] for r in lo.records] == [0]
    assert [r.data["n"] for r in hi.records] == [1, 2]


def test_graph_missing_source_connector_raises() -> None:
    nodes = [
        GraphNode(id="s", kind="source", source_name="missing"),
        GraphNode(id="k", kind="sink", sink=SinkSpec(name="snk", table="o")),
    ]
    edges = [GraphEdge("s", "k")]
    with pytest.raises(TaskError, match="graph source"):
        Pipeline("g").add(_graph_task(nodes, edges)).run(connectors={"snk": InMemoryBatchSink()})


def test_graph_cursor_unsupported() -> None:
    nodes = [
        GraphNode(id="s", kind="source", source_name="src"),
        GraphNode(id="k", kind="sink", sink=SinkSpec(name="snk", table="o")),
    ]
    edges = [GraphEdge("s", "k")]
    with pytest.raises(TaskError, match="cursor"):
        Pipeline("g").add(_graph_task(nodes, edges)).run(
            connectors={"src": _src("a"), "snk": InMemoryBatchSink()}, cursor_from=1
        )


# ---------- GraphConfig validation ----------


def _node(id: str, type: str, **kw: object) -> dict:
    return {"id": id, "type": type, **kw}


def test_graph_config_requires_single_source() -> None:
    with pytest.raises(ValueError, match="exactly one source"):
        GraphConfig.model_validate(
            {
                "nodes": [
                    _node("s1", "source", connection="a"),
                    _node("s2", "source", connection="b"),
                    _node("k", "sink", connection="c"),
                ],
                "edges": [{"from_node": "s1", "to_node": "k"}],
            }
        )


def test_graph_config_rejects_fan_in() -> None:
    with pytest.raises(ValueError, match="exactly one incoming edge"):
        GraphConfig.model_validate(
            {
                "nodes": [
                    _node("s", "source", connection="a"),
                    _node("t", "transform", transform={"type": "rename", "mapping": {}}),
                    _node("k", "sink", connection="c"),
                ],
                "edges": [
                    {"from_node": "s", "to_node": "k"},
                    {"from_node": "t", "to_node": "k"},  # second incoming → fan-in
                ],
            }
        )


def test_pipeline_config_rejects_graph_with_single_or_tasks() -> None:
    with pytest.raises(ValueError, match="exactly one of"):
        PipelineConfig.model_validate(
            {
                "name": "p",
                "source": {"connection": "s"},
                "sink": {"connection": "k"},
                "graph": {
                    "nodes": [
                        _node("s", "source", connection="s"),
                        _node("k", "sink", connection="k"),
                    ],
                    "edges": [{"from_node": "s", "to_node": "k"}],
                },
            }
        )
