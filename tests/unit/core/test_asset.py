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
    # A SQL source keys to its FROM table so it matches a table-based sink.
    src_key = AssetKey.of("src", "public.orders")
    dst_key = AssetKey.of("dst", "public.orders_copy")
    assert lin.inputs == [src_key]
    assert lin.outputs == [dst_key]
    assert lin.edges == [LineageEdge(src_key, dst_key)]


def test_derive_lineage_query_without_from_falls_back() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "mongo", "query": "users"},  # collection, no FROM
            "sink": {"connection": "dst", "table": "out", "mode": "append"},
        }
    )
    lin = derive_lineage(cfg)
    assert lin.inputs == [AssetKey.of("mongo", "users")]


def test_source_query_key_matches_sink_table_key() -> None:
    """The whole point of FROM-parsing: a pipeline reading `SELECT * FROM orders`
    produces the same key a sink writing to `orders` does — so lineage links."""
    from etl_plugins.core.asset import derive_asset_key

    src = derive_asset_key("wh", {"query": "SELECT a, b FROM orders WHERE x > 1"})
    sink = derive_asset_key("wh", {"table": "orders"})
    assert src == sink == AssetKey.of("wh", "orders")


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


def test_derive_lineage_records_kinds() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "kafka", "topic": "events"},
            "sink": {"connection": "lake", "key": "exports/out.parquet", "mode": "append"},
        }
    )
    lin = derive_lineage(cfg)
    assert lin.kinds[AssetKey.of("kafka", "events")] == "topic"
    assert lin.kinds[AssetKey.of("lake", "exports/out.parquet")] == "object"


def test_derive_lineage_join_query_registers_every_base_table() -> None:
    """Phase X follow-up (2026-05-28): a JOIN'd source query auto-registers
    *both* base tables as input assets, and every one gets an edge to the
    sink. Without this the catalog graph would only see the regex-picked
    first FROM table, breaking the column-lineage / asset-lineage contract."""
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {
                "connection": "wh",
                "query": "SELECT t1.a, t2.b FROM t1 JOIN t2 ON t1.id = t2.id",
            },
            "sink": {"connection": "wh", "table": "joined", "mode": "append"},
        }
    )
    lin = derive_lineage(cfg)
    t1 = AssetKey.of("wh", "t1")
    t2 = AssetKey.of("wh", "t2")
    joined = AssetKey.of("wh", "joined")
    assert set(lin.inputs) == {t1, t2}
    assert lin.outputs == [joined]
    assert LineageEdge(t1, joined) in lin.edges
    assert LineageEdge(t2, joined) in lin.edges
    # First-seen order: the regex-keyed primary lands first.
    assert lin.inputs[0] == t1


def test_derive_lineage_cte_registers_only_base_tables() -> None:
    """CTE names are internal aliases — they shouldn't pollute the input
    asset set. Only the base table(s) the CTEs read from should land."""
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {
                "connection": "wh",
                "query": (
                    "WITH a AS (SELECT * FROM raw), " "b AS (SELECT * FROM a) " "SELECT * FROM b"
                ),
            },
            "sink": {"connection": "wh", "table": "out", "mode": "append"},
        }
    )
    lin = derive_lineage(cfg)
    assert set(lin.inputs) == {AssetKey.of("wh", "raw")}


def test_derive_lineage_schema_qualified_join_dedupes_with_primary() -> None:
    """The primary-key derivation (regex-based) preserves the schema
    qualifier; the new sqlglot pass must produce the *same* keys for the
    same tables — otherwise a single ``public.orders`` table would show
    up as two distinct catalog rows."""
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {
                "connection": "wh",
                "query": (
                    "SELECT o.id, c.email "
                    "FROM public.orders o JOIN public.customers c "
                    "ON o.customer_id = c.id"
                ),
            },
            "sink": {"connection": "wh", "table": "joined", "mode": "append"},
        }
    )
    lin = derive_lineage(cfg)
    assert set(lin.inputs) == {
        AssetKey.of("wh", "public.orders"),
        AssetKey.of("wh", "public.customers"),
    }


def test_derive_lineage_graph_source_with_join_query() -> None:
    """Graph-shape pipelines apply the same auto-fanout — every JOIN'd table
    on a source node lands as an input + gets an edge to the sink(s)."""
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "graph": {
                "nodes": [
                    {
                        "id": "s",
                        "type": "source",
                        "connection": "wh",
                        "query": "SELECT a.x, b.y FROM a LEFT JOIN b ON a.id = b.id",
                    },
                    {"id": "k", "type": "sink", "connection": "wh", "table": "merged"},
                ],
                "edges": [{"from_node": "s", "to_node": "k"}],
            },
        }
    )
    lin = derive_lineage(cfg)
    a = AssetKey.of("wh", "a")
    b = AssetKey.of("wh", "b")
    merged = AssetKey.of("wh", "merged")
    assert set(lin.inputs) == {a, b}
    assert lin.outputs == [merged]
    assert LineageEdge(a, merged) in lin.edges
    assert LineageEdge(b, merged) in lin.edges


def test_derive_lineage_non_sql_source_unchanged() -> None:
    """A non-SQL source (Mongo collection name, Kafka topic, S3 key) goes
    through the primary key derivation alone — no auto-fanout because there's
    nothing to parse. Guards against accidentally adding ghost input assets."""
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "mongo", "query": "users"},  # collection name
            "sink": {"connection": "dst", "table": "out", "mode": "append"},
        }
    )
    lin = derive_lineage(cfg)
    assert lin.inputs == [AssetKey.of("mongo", "users")]


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


# ---------- Pipeline.lineage() + runtime emit (A2) ----------


def _idem_pipeline() -> object:
    from etl_plugins.runtime.builder import build_pipeline
    from tests.fixtures.connectors import InMemoryBatchSink, InMemoryBatchSource

    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "src", "table": "orders"},
            "sink": {"connection": "dst", "table": "out", "mode": "append"},
        }
    )
    return build_pipeline(pc, {"src": InMemoryBatchSource(), "dst": InMemoryBatchSink()})


def test_pipeline_lineage_derives_from_tasks() -> None:
    pipeline, _ = _idem_pipeline()
    lin = pipeline.lineage()
    assert lin.inputs == [AssetKey.of("src", "orders")]
    assert lin.outputs == [AssetKey.of("dst", "out")]
    assert lin.edges == [LineageEdge(AssetKey.of("src", "orders"), AssetKey.of("dst", "out"))]


def test_pipeline_lineage_normalises_split_sink_key() -> None:
    """A sink split to '<conn>::sink' (ADR-0034) reports the original conn."""
    from etl_plugins.runtime.builder import build_pipeline
    from tests.fixtures.connectors import InMemoryBatchSink

    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "db", "table": "orders"},
            "sink": {"connection": "db", "table": "out", "mode": "append"},
        }
    )
    pipeline, _ = build_pipeline(
        pc, {"db": InMemoryBatchSink()}, connector_factory=lambda _n: InMemoryBatchSink()
    )
    lin = pipeline.lineage()
    assert AssetKey.of("db", "out") in lin.outputs
    assert all("::sink" not in str(k) for k in lin.outputs)


def test_run_emits_start_and_complete() -> None:
    from etl_plugins.observability.lineage import (
        COMPLETE,
        START,
        CollectingLineageEmitter,
        reset_lineage_emitter,
        set_lineage_emitter,
    )

    pipeline, connectors = _idem_pipeline()
    em = CollectingLineageEmitter()
    set_lineage_emitter(em)
    try:
        for c in connectors.values():
            c.connect()
        pipeline.run(connectors=connectors)
    finally:
        for c in connectors.values():
            c.close()
        reset_lineage_emitter()

    assert [e.event_type for e in em.events] == [START, COMPLETE]
    assert em.events[0].inputs == (AssetKey.of("src", "orders"),)
    assert em.events[0].outputs == (AssetKey.of("dst", "out"),)
    assert em.events[1].records_read is not None


def test_run_emits_fail_on_error() -> None:
    from etl_plugins.core.exceptions import TaskError
    from etl_plugins.observability.lineage import (
        FAIL,
        START,
        CollectingLineageEmitter,
        reset_lineage_emitter,
        set_lineage_emitter,
    )

    pipeline, connectors = _idem_pipeline()
    del connectors["dst"]  # force a load failure
    em = CollectingLineageEmitter()
    set_lineage_emitter(em)
    try:
        for c in connectors.values():
            c.connect()
        with pytest.raises(TaskError):
            pipeline.run(connectors=connectors)
    finally:
        for c in connectors.values():
            c.close()
        reset_lineage_emitter()

    assert [e.event_type for e in em.events] == [START, FAIL]
    assert em.events[1].error is not None
