"""Unified graph model + lowering (ADR-0041, G1).

Covers the generalized :class:`GraphConfig` validation (join/fan-in/multi-source/
cycles), :func:`to_graph` lowering of the single-task shape, ``topological_order``,
and the builder guard that fails fast on shapes the current re-read engine can't
run (multi-source / join) until the materialize engine (G2).
"""

from __future__ import annotations

import pytest

from etl_plugins.config.models import GraphConfig, PipelineConfig
from etl_plugins.core.exceptions import ConfigError
from etl_plugins.runtime.builder import build_pipeline
from etl_plugins.runtime.graph import to_graph, topological_order


def _node(id: str, type: str, **kw: object) -> dict:
    return {"id": id, "type": type, **kw}


# ---------- generalized GraphConfig validation (ADR-0041) ----------


def test_join_node_requires_two_incoming() -> None:
    with pytest.raises(ValueError, match="at least two incoming"):
        GraphConfig.model_validate(
            {
                "nodes": [
                    _node("s", "source", connection="a"),
                    _node("j", "join", on=["id"]),
                    _node("k", "sink", connection="c"),
                ],
                "edges": [
                    {"from_node": "s", "to_node": "j"},
                    {"from_node": "j", "to_node": "k"},
                ],
            }
        )


def test_join_accepts_two_incoming() -> None:
    GraphConfig.model_validate(
        {
            "nodes": [
                _node("s1", "source", connection="a"),
                _node("s2", "source", connection="b"),
                _node("j", "join", on=["id"], how="left"),
                _node("k", "sink", connection="c"),
            ],
            "edges": [
                {"from_node": "s1", "to_node": "j"},
                {"from_node": "s2", "to_node": "j"},
                {"from_node": "j", "to_node": "k"},
            ],
        }
    )


def test_unknown_node_type_rejected() -> None:
    with pytest.raises(ValueError, match="unknown type"):
        GraphConfig.model_validate({"nodes": [_node("x", "frobnicate")], "edges": []})


def test_bad_join_how_rejected() -> None:
    with pytest.raises(ValueError, match="unknown how"):
        GraphConfig.model_validate({"nodes": [_node("j", "join", how="sideways")], "edges": []})


def test_cycle_rejected() -> None:
    # s→k is a valid path; t1↔t2 is an isolated 2-cycle (both indegree 1, so the
    # indegree rules pass and only the acyclicity check catches it).
    with pytest.raises(ValueError, match="cycle"):
        GraphConfig.model_validate(
            {
                "nodes": [
                    _node("s", "source", connection="a"),
                    _node("k", "sink", connection="c"),
                    _node("t1", "transform", transform={"type": "rename", "mapping": {}}),
                    _node("t2", "transform", transform={"type": "rename", "mapping": {}}),
                ],
                "edges": [
                    {"from_node": "s", "to_node": "k"},
                    {"from_node": "t1", "to_node": "t2"},
                    {"from_node": "t2", "to_node": "t1"},
                ],
            }
        )


def test_requires_source_and_sink() -> None:
    with pytest.raises(ValueError, match="at least one source"):
        GraphConfig.model_validate({"nodes": [_node("k", "sink", connection="c")], "edges": []})
    with pytest.raises(ValueError, match="at least one sink"):
        GraphConfig.model_validate({"nodes": [_node("s", "source", connection="a")], "edges": []})


# ---------- to_graph: single-task lowering ----------


def _single_task(**over: object) -> PipelineConfig:
    base: dict = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT * FROM t", "cursor_column": "id"},
        "transforms": [
            {"type": "rename", "mapping": {"a": "b"}},
            {"type": "filter", "expr": "True"},
        ],
        "sink": {"connection": "dst", "table": "out"},
    }
    base.update(over)
    return PipelineConfig.model_validate(base)


def test_to_graph_lowers_linear_chain() -> None:
    g = to_graph(_single_task())
    assert [n.id for n in g.nodes] == ["source", "transform_0", "transform_1", "sink_0"]
    assert [(e.from_node, e.to_node) for e in g.edges] == [
        ("source", "transform_0"),
        ("transform_0", "transform_1"),
        ("transform_1", "sink_0"),
    ]
    src = g.nodes[0]
    dumped = src.model_dump()
    assert src.type == "source"
    assert src.connection == "src"
    assert src.query == "SELECT * FROM t"
    assert dumped["cursor_column"] == "id"  # extra source fields ride along


def test_to_graph_is_valid_graph_config() -> None:
    # Lowering returns a GraphConfig, so it re-runs the graph validator.
    assert isinstance(to_graph(_single_task()), GraphConfig)


def test_to_graph_fanout_carries_sink_when_to_edge() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "src"},
            "sinks": [
                {"connection": "a", "table": "o", "when": "data['x'] > 0"},
                {"connection": "b", "table": "o"},
            ],
        }
    )
    g = to_graph(cfg)
    assert [n.id for n in g.nodes] == ["source", "sink_0", "sink_1"]
    whens = {(e.from_node, e.to_node): e.when for e in g.edges}
    assert whens[("source", "sink_0")] == "data['x'] > 0"
    assert whens[("source", "sink_1")] is None


def test_to_graph_passthrough_for_graph_shape() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "graph": {
                "nodes": [
                    _node("s", "source", connection="a"),
                    _node("k", "sink", connection="c"),
                ],
                "edges": [{"from_node": "s", "to_node": "k"}],
            },
        }
    )
    assert to_graph(cfg) is cfg.graph


def test_to_graph_task_dag_deferred() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                {
                    "name": "a",
                    "source": {"connection": "s"},
                    "sink": {"connection": "k", "table": "o"},
                },
            ],
        }
    )
    with pytest.raises(NotImplementedError, match="Phase H"):
        to_graph(cfg)


def test_topological_order_respects_dependencies() -> None:
    order = topological_order(to_graph(_single_task()))
    assert order.index("source") < order.index("transform_0")
    assert order.index("transform_0") < order.index("transform_1")
    assert order.index("transform_1") < order.index("sink_0")


# ---------- builder guard: relaxed shapes the re-read engine can't run yet ----------


def test_build_rejects_multi_source_until_g2() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "graph": {
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
            },
        }
    )
    with pytest.raises(ConfigError, match="materialize engine"):
        build_pipeline(cfg, connectors={})


def test_build_rejects_join_until_g2() -> None:
    # Single source, fan-out then join → not multi-source, but join present.
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "graph": {
                "nodes": [
                    _node("s", "source", connection="a"),
                    _node("t1", "transform", transform={"type": "rename", "mapping": {}}),
                    _node("t2", "transform", transform={"type": "rename", "mapping": {}}),
                    _node("j", "join", on=["id"]),
                    _node("k", "sink", connection="c"),
                ],
                "edges": [
                    {"from_node": "s", "to_node": "t1"},
                    {"from_node": "s", "to_node": "t2"},
                    {"from_node": "t1", "to_node": "j"},
                    {"from_node": "t2", "to_node": "j"},
                    {"from_node": "j", "to_node": "k"},
                ],
            },
        }
    )
    with pytest.raises(ConfigError, match="join nodes need the materialize engine"):
        build_pipeline(cfg, connectors={})
