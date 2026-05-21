"""Asset model + static lineage derivation (ADR-0024 / ADR-0036)."""

from __future__ import annotations

import pytest

from etl_plugins.config.models import PipelineConfig
from etl_plugins.core.asset import AssetGraph, AssetKey, AssetLineage, LineageEdge
from etl_plugins.runtime.lineage import derive_lineage

# ---------- AssetKey ----------


def test_asset_key_render_and_parse() -> None:
    k = AssetKey.of("warehouse", "public.orders")
    assert str(k) == "warehouse/public.orders"
    assert AssetKey.parse("warehouse/public.orders") == k


def test_asset_key_of_drops_empty_parts() -> None:
    assert AssetKey.of("conn", "") == AssetKey(("conn",))


def test_asset_key_rejects_empty() -> None:
    with pytest.raises(ValueError):
        AssetKey(())
    with pytest.raises(ValueError):
        AssetKey(("",))


def test_asset_key_hashable_and_equal() -> None:
    a = AssetKey.of("c", "t")
    b = AssetKey.of("c", "t")
    assert a == b
    assert len({a, b}) == 1


# ---------- AssetGraph ----------


def test_asset_graph_upstream_downstream() -> None:
    g = AssetGraph()
    x, y, z = AssetKey.of("x"), AssetKey.of("y"), AssetKey.of("z")
    g.add_edge(x, y)
    g.add_edge(y, z)
    assert g.downstream(x) == {y}
    assert g.upstream(z) == {y}
    assert g.descendants(x) == {y, z}
    assert g.ancestors(z) == {x, y}


def test_asset_graph_cycle_safe() -> None:
    g = AssetGraph()
    a, b = AssetKey.of("a"), AssetKey.of("b")
    g.add_edge(a, b)
    g.add_edge(b, a)  # cycle
    assert g.descendants(a) == {a, b}  # terminates


def test_asset_graph_connects_pipelines_via_shared_key() -> None:
    """Cross-pipeline lineage emerges when two pipelines share an asset key."""
    raw = AssetKey.of("lake", "raw.events")
    staged = AssetKey.of("wh", "staging.events")
    mart = AssetKey.of("wh", "mart.daily")
    g = AssetGraph()
    g.add_lineage(AssetLineage(inputs=[raw], outputs=[staged], edges=[LineageEdge(raw, staged)]))
    g.add_lineage(AssetLineage(inputs=[staged], outputs=[mart], edges=[LineageEdge(staged, mart)]))
    assert g.ancestors(mart) == {raw, staged}


# ---------- derive_lineage ----------


def test_derive_lineage_single_task() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "src", "query": "SELECT * FROM public.orders"},
            "sink": {"connection": "dst", "table": "public.orders_copy", "mode": "append"},
        }
    )
    lin = derive_lineage(cfg)
    src_key = AssetKey.of("src", "SELECT * FROM public.orders")
    dst_key = AssetKey.of("dst", "public.orders_copy")
    assert lin.inputs == [src_key]
    assert lin.outputs == [dst_key]
    assert lin.edges == [LineageEdge(src_key, dst_key)]


def test_derive_lineage_prefers_table_over_query() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "src", "table": "orders", "query": "SELECT 1"},
            "sink": {"connection": "dst", "table": "out", "mode": "append"},
        }
    )
    lin = derive_lineage(cfg)
    assert lin.inputs == [AssetKey.of("src", "orders")]  # table wins over query


def test_derive_lineage_fanout() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "src", "table": "orders"},
            "sinks": [
                {"connection": "a", "table": "t1", "mode": "append"},
                {"connection": "b", "table": "t2", "mode": "append"},
            ],
        }
    )
    lin = derive_lineage(cfg)
    src = AssetKey.of("src", "orders")
    assert lin.inputs == [src]
    assert set(lin.outputs) == {AssetKey.of("a", "t1"), AssetKey.of("b", "t2")}
    assert LineageEdge(src, AssetKey.of("a", "t1")) in lin.edges
    assert LineageEdge(src, AssetKey.of("b", "t2")) in lin.edges


def test_derive_lineage_graph_shape() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "mode": "batch",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "source", "connection": "src", "table": "orders"},
                    {
                        "id": "f",
                        "type": "transform",
                        "transform": {"type": "filter", "expr": "True"},
                    },
                    {"id": "k", "type": "sink", "connection": "dst", "table": "out"},
                ],
                "edges": [
                    {"from_node": "s", "to_node": "f"},
                    {"from_node": "f", "to_node": "k"},
                ],
            },
        }
    )
    lin = derive_lineage(cfg)
    assert lin.inputs == [AssetKey.of("src", "orders")]
    assert lin.outputs == [AssetKey.of("dst", "out")]
    assert lin.edges == [LineageEdge(AssetKey.of("src", "orders"), AssetKey.of("dst", "out"))]


def test_derive_lineage_kafka_topic_and_s3_key() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "kafka", "topic": "events"},
            "sink": {"connection": "lake", "key": "exports/out.parquet", "mode": "append"},
        }
    )
    lin = derive_lineage(cfg)
    assert lin.inputs == [AssetKey.of("kafka", "events")]
    assert lin.outputs == [AssetKey.of("lake", "exports/out.parquet")]
