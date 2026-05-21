"""Dataflow graph execution (ADR-0030) — branching at any node, free order."""

from __future__ import annotations

import pytest

from etl_plugins.config.models import GraphConfig, PipelineConfig
from etl_plugins.core.exceptions import TaskError
from etl_plugins.core.pipeline import (
    AggSpec,
    GraphEdge,
    GraphNode,
    Pipeline,
    SinkSpec,
    Task,
    _join_records,
)
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


# ---------- materialize engine: multi-source + join (ADR-0041, G2) ----------


def _rows(*rows: dict) -> InMemoryBatchSource:
    return InMemoryBatchSource([Record(data=r) for r in rows])


def test_graph_multi_source_inner_join() -> None:
    nodes = [
        GraphNode(id="l", kind="source", source_name="L"),
        GraphNode(id="r", kind="source", source_name="R"),
        GraphNode(id="j", kind="join", join_on=["id"], join_how="inner"),
        GraphNode(id="k", kind="sink", sink=SinkSpec(name="snk", table="o")),
    ]
    edges = [GraphEdge("l", "j"), GraphEdge("r", "j"), GraphEdge("j", "k")]
    snk = InMemoryBatchSink()
    result = (
        Pipeline("g")
        .add(_graph_task(nodes, edges))
        .run(
            connectors={
                "L": _rows({"id": 1, "a": "x"}, {"id": 2, "a": "y"}),
                "R": _rows({"id": 1, "b": "P"}, {"id": 3, "b": "Q"}),
                "snk": snk,
            }
        )
    )
    assert result.success
    assert result.records_read == 4  # both sources, read once each
    assert [r.data for r in snk.records] == [{"id": 1, "a": "x", "b": "P"}]
    assert result.records_written == 1


def test_graph_left_join_keeps_unmatched_left() -> None:
    nodes = [
        GraphNode(id="l", kind="source", source_name="L"),
        GraphNode(id="r", kind="source", source_name="R"),
        GraphNode(id="j", kind="join", join_on=["id"], join_how="left"),
        GraphNode(id="k", kind="sink", sink=SinkSpec(name="snk", table="o")),
    ]
    edges = [GraphEdge("l", "j"), GraphEdge("r", "j"), GraphEdge("j", "k")]
    snk = InMemoryBatchSink()
    Pipeline("g").add(_graph_task(nodes, edges)).run(
        connectors={
            "L": _rows({"id": 1, "a": "x"}, {"id": 2, "a": "y"}),
            "R": _rows({"id": 1, "b": "P"}),
            "snk": snk,
        }
    )
    assert sorted((r.data for r in snk.records), key=lambda d: d["id"]) == [
        {"id": 1, "a": "x", "b": "P"},
        {"id": 2, "a": "y"},
    ]


def test_graph_join_requires_two_inputs() -> None:
    nodes = [
        GraphNode(id="s", kind="source", source_name="src"),
        GraphNode(id="j", kind="join", join_on=["id"]),
        GraphNode(id="k", kind="sink", sink=SinkSpec(name="snk", table="o")),
    ]
    edges = [GraphEdge("s", "j"), GraphEdge("j", "k")]
    with pytest.raises(TaskError, match="at least two inputs"):
        Pipeline("g").add(_graph_task(nodes, edges)).run(
            connectors={"src": _rows({"id": 1}), "snk": InMemoryBatchSink()}
        )


def test_graph_fan_in_join_reads_single_source_once() -> None:
    # source feeds two transform branches that re-join → the source is read
    # exactly once (materialized), unlike the old per-sink re-read.
    def add_x(r: Record) -> Record:
        return Record(data={**r.data, "x": 1}, metadata=r.metadata)

    def add_y(r: Record) -> Record:
        return Record(data={**r.data, "y": 2}, metadata=r.metadata)

    nodes = [
        GraphNode(id="s", kind="source", source_name="src"),
        GraphNode(id="t1", kind="transform", transform_fn=add_x),
        GraphNode(id="t2", kind="transform", transform_fn=add_y),
        GraphNode(id="j", kind="join", join_on=["id"], join_how="inner"),
        GraphNode(id="k", kind="sink", sink=SinkSpec(name="snk", table="o")),
    ]
    edges = [
        GraphEdge("s", "t1"),
        GraphEdge("s", "t2"),
        GraphEdge("t1", "j"),
        GraphEdge("t2", "j"),
        GraphEdge("j", "k"),
    ]
    snk = InMemoryBatchSink()
    result = (
        Pipeline("g")
        .add(_graph_task(nodes, edges))
        .run(connectors={"src": _rows({"id": 1}, {"id": 2}), "snk": snk})
    )
    assert result.records_read == 2  # single source, read once
    assert sorted((r.data for r in snk.records), key=lambda d: d["id"]) == [
        {"id": 1, "x": 1, "y": 2},
        {"id": 2, "x": 1, "y": 2},
    ]


def test_graph_multi_source_independent_sinks() -> None:
    nodes = [
        GraphNode(id="s1", kind="source", source_name="A"),
        GraphNode(id="s2", kind="source", source_name="B"),
        GraphNode(id="k1", kind="sink", sink=SinkSpec(name="ka", table="o")),
        GraphNode(id="k2", kind="sink", sink=SinkSpec(name="kb", table="o")),
    ]
    edges = [GraphEdge("s1", "k1"), GraphEdge("s2", "k2")]
    ka, kb = InMemoryBatchSink(), InMemoryBatchSink()
    result = (
        Pipeline("g")
        .add(_graph_task(nodes, edges))
        .run(
            connectors={
                "A": _rows({"id": 1}),
                "B": _rows({"id": 2}, {"id": 3}),
                "ka": ka,
                "kb": kb,
            }
        )
    )
    assert result.records_read == 3
    assert [r.data["id"] for r in ka.records] == [1]
    assert [r.data["id"] for r in kb.records] == [2, 3]


def test_join_records_outer() -> None:
    out = _join_records(
        [Record(data={"id": 1, "a": "x"}), Record(data={"id": 2, "a": "y"})],
        [Record(data={"id": 1, "b": "P"}), Record(data={"id": 3, "b": "Q"})],
        ["id"],
        "outer",
    )
    assert sorted((r.data for r in out), key=lambda d: d["id"]) == [
        {"id": 1, "a": "x", "b": "P"},
        {"id": 2, "a": "y"},
        {"id": 3, "b": "Q"},
    ]


def test_join_records_requires_on() -> None:
    with pytest.raises(TaskError, match="non-empty 'on'"):
        _join_records([], [], [], "inner")


# ---------- aggregate operator (ADR-0041, G3) ----------


def test_graph_aggregate_group_by() -> None:
    nodes = [
        GraphNode(id="s", kind="source", source_name="src"),
        GraphNode(
            id="agg",
            kind="aggregate",
            agg_group_by=["g"],
            aggregations=[
                AggSpec(op="sum", name="total", column="v"),
                AggSpec(op="count", name="n"),
            ],
        ),
        GraphNode(id="k", kind="sink", sink=SinkSpec(name="snk", table="o")),
    ]
    edges = [GraphEdge("s", "agg"), GraphEdge("agg", "k")]
    snk = InMemoryBatchSink()
    Pipeline("g").add(_graph_task(nodes, edges)).run(
        connectors={
            "src": _rows(
                {"g": "a", "v": 1},
                {"g": "a", "v": 2},
                {"g": "b", "v": 5},
            ),
            "snk": snk,
        }
    )
    assert sorted((r.data for r in snk.records), key=lambda d: d["g"]) == [
        {"g": "a", "total": 3, "n": 2},
        {"g": "b", "total": 5, "n": 1},
    ]


def test_graph_aggregate_global_avg() -> None:
    nodes = [
        GraphNode(id="s", kind="source", source_name="src"),
        GraphNode(
            id="agg",
            kind="aggregate",
            agg_group_by=[],
            aggregations=[
                AggSpec(op="avg", name="mean", column="v"),
                AggSpec(op="max", name="hi", column="v"),
            ],
        ),
        GraphNode(id="k", kind="sink", sink=SinkSpec(name="snk", table="o")),
    ]
    edges = [GraphEdge("s", "agg"), GraphEdge("agg", "k")]
    snk = InMemoryBatchSink()
    Pipeline("g").add(_graph_task(nodes, edges)).run(
        connectors={"src": _rows({"v": 2}, {"v": 4}, {"v": 6}), "snk": snk}
    )
    assert [r.data for r in snk.records] == [{"mean": 4.0, "hi": 6}]


# ---------- GraphConfig validation ----------


def _node(id: str, type: str, **kw: object) -> dict:
    return {"id": id, "type": type, **kw}


def test_graph_config_allows_multi_source_via_join() -> None:
    # ADR-0041: multiple sources converging at a join node is now a valid shape.
    GraphConfig.model_validate(
        {
            "nodes": [
                _node("s1", "source", connection="a"),
                _node("s2", "source", connection="b"),
                _node("j", "join", on=["id"]),
                _node("k", "sink", connection="c"),
            ],
            "edges": [
                {"from_node": "s1", "to_node": "j"},
                {"from_node": "s2", "to_node": "j"},
                {"from_node": "j", "to_node": "k"},
            ],
        }
    )


def test_graph_config_rejects_fan_in_without_join() -> None:
    # Fan-in into a transform/sink (not a join) is still rejected — merge must
    # go through an explicit join node so single-input semantics stay clear.
    with pytest.raises(ValueError, match="join"):
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
