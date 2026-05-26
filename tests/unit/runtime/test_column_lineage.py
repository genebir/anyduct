"""Column-level lineage derivation (ADR-0041, Phase J1)."""

from __future__ import annotations

from etl_plugins.config.models import PipelineConfig
from etl_plugins.core.asset import AssetKey
from etl_plugins.runtime.column_lineage import derive_column_lineage


def _cfg(**over: object) -> PipelineConfig:
    base: dict = {
        "name": "p",
        "source": {"connection": "wh", "query": "SELECT a, b FROM t1"},
        "sink": {"connection": "wh", "table": "t2"},
    }
    base.update(over)
    return PipelineConfig.model_validate(base)


def _edge_map(lineage) -> dict[str, tuple[str, ...]]:
    """{downstream-col: (upstream-col, ...)} for compact assertions."""
    return {str(e.downstream): tuple(str(u) for u in e.upstreams) for e in lineage.edges}


# ---------- simple SQL → 1:1 edges ----------


def test_simple_select_emits_per_column_edges() -> None:
    out = _edge_map(derive_column_lineage(_cfg()))
    assert out == {
        "wh/t2:a": ("wh/t1:a",),
        "wh/t2:b": ("wh/t1:b",),
    }


def test_alias_remaps_output_column() -> None:
    out = _edge_map(
        derive_column_lineage(_cfg(source={"connection": "wh", "query": "SELECT a AS x FROM t1"}))
    )
    assert out == {"wh/t2:x": ("wh/t1:a",)}


def test_complex_expression_keeps_column_drops_upstream() -> None:
    # UPPER(b) isn't simple-traceable → column exists, no upstream.
    out = _edge_map(
        derive_column_lineage(
            _cfg(
                source={"connection": "wh", "query": "SELECT a, UPPER(b) AS bx FROM t1"},
            )
        )
    )
    assert out == {"wh/t2:a": ("wh/t1:a",), "wh/t2:bx": ()}


# ---------- declarative transforms ----------


def test_rename_remaps_output_columns() -> None:
    out = _edge_map(
        derive_column_lineage(_cfg(transforms=[{"type": "rename", "mapping": {"a": "id"}}]))
    )
    assert out == {"wh/t2:id": ("wh/t1:a",), "wh/t2:b": ("wh/t1:b",)}


def test_select_keeps_only_listed_columns() -> None:
    out = _edge_map(derive_column_lineage(_cfg(transforms=[{"type": "select", "columns": ["a"]}])))
    assert out == {"wh/t2:a": ("wh/t1:a",)}


def test_drop_removes_columns() -> None:
    out = _edge_map(derive_column_lineage(_cfg(transforms=[{"type": "drop", "columns": ["a"]}])))
    assert out == {"wh/t2:b": ("wh/t1:b",)}


def test_cast_is_passthrough_for_column_set() -> None:
    out = _edge_map(
        derive_column_lineage(_cfg(transforms=[{"type": "cast", "columns": {"a": "int"}}]))
    )
    assert out == {"wh/t2:a": ("wh/t1:a",), "wh/t2:b": ("wh/t1:b",)}


def test_add_constant_introduces_column_without_upstream() -> None:
    out = _edge_map(
        derive_column_lineage(
            _cfg(transforms=[{"type": "add_constant", "column": "tenant", "value": "x"}])
        )
    )
    assert out == {
        "wh/t2:a": ("wh/t1:a",),
        "wh/t2:b": ("wh/t1:b",),
        "wh/t2:tenant": (),
    }


# ---------- opaque cases ----------


def _is_opaque(cfg: PipelineConfig, key: AssetKey) -> bool:
    return any(k == key for k in derive_column_lineage(cfg).opaque_assets)


def test_python_transform_marks_sink_opaque() -> None:
    cfg = _cfg(transforms=[{"type": "python", "callable": "m:f"}])
    lineage = derive_column_lineage(cfg)
    assert lineage.edges == []
    assert AssetKey.of("wh", "t2") in lineage.opaque_assets


def test_select_star_marks_sink_opaque() -> None:
    cfg = _cfg(source={"connection": "wh", "query": "SELECT * FROM t1"})
    assert _is_opaque(cfg, AssetKey.of("wh", "t2"))


def test_join_marks_sink_opaque() -> None:
    cfg = _cfg(
        source={"connection": "wh", "query": "SELECT a, b FROM t1 JOIN t2 USING (id)"},
    )
    assert _is_opaque(cfg, AssetKey.of("wh", "t2"))


def test_no_query_direct_table_read_is_opaque() -> None:
    # No SQL → no column enumeration. (Could fall back to schema introspection
    # in a later slice; v1 calls it opaque.)
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "wh", "table": "t1"},  # no query
            "sink": {"connection": "wh", "table": "t2"},
        }
    )
    assert _is_opaque(cfg, AssetKey.of("wh", "t2"))


def test_unparseable_query_is_opaque() -> None:
    cfg = _cfg(source={"connection": "wh", "query": "this is not sql at all !!"})
    assert _is_opaque(cfg, AssetKey.of("wh", "t2"))


def test_sql_exec_transform_is_opaque() -> None:
    cfg = _cfg(transforms=[{"type": "sql_exec", "connection": "wh", "statement": "VACUUM t1"}])
    assert _is_opaque(cfg, AssetKey.of("wh", "t2"))


# ---------- graph shape ----------


def test_graph_linear_chain_propagates_through_transforms() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "graph": {
                "nodes": [
                    {
                        "id": "s",
                        "type": "source",
                        "connection": "wh",
                        "query": "SELECT a, b FROM t1",
                    },
                    {
                        "id": "r",
                        "type": "transform",
                        "transform": {"type": "rename", "mapping": {"a": "id"}},
                    },
                    {"id": "k", "type": "sink", "connection": "wh", "table": "t2"},
                ],
                "edges": [
                    {"from_node": "s", "to_node": "r"},
                    {"from_node": "r", "to_node": "k"},
                ],
            },
        }
    )
    out = _edge_map(derive_column_lineage(cfg))
    assert out == {"wh/t2:id": ("wh/t1:a",), "wh/t2:b": ("wh/t1:b",)}


def test_graph_join_marks_all_sinks_opaque() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "graph": {
                "nodes": [
                    {
                        "id": "s1",
                        "type": "source",
                        "connection": "wh",
                        "query": "SELECT id, a FROM t1",
                    },
                    {
                        "id": "s2",
                        "type": "source",
                        "connection": "wh",
                        "query": "SELECT id, b FROM t2",
                    },
                    {"id": "j", "type": "join", "on": ["id"]},
                    {"id": "k", "type": "sink", "connection": "wh", "table": "joined"},
                ],
                "edges": [
                    {"from_node": "s1", "to_node": "j"},
                    {"from_node": "s2", "to_node": "j"},
                    {"from_node": "j", "to_node": "k"},
                ],
            },
        }
    )
    lineage = derive_column_lineage(cfg)
    assert lineage.edges == []
    assert AssetKey.of("wh", "joined") in lineage.opaque_assets


# ---------- task-DAG ----------


def test_task_dag_processes_each_task() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                {
                    "name": "t_extract",
                    "source": {"connection": "wh", "query": "SELECT a FROM raw"},
                    "sink": {"connection": "wh", "table": "staging"},
                },
                {
                    "name": "t_load",
                    "source": {"connection": "wh", "query": "SELECT a FROM staging"},
                    "sink": {"connection": "wh", "table": "mart"},
                    "depends_on": ["t_extract"],
                },
            ],
        }
    )
    out = _edge_map(derive_column_lineage(cfg))
    assert out == {
        "wh/staging:a": ("wh/raw:a",),
        "wh/mart:a": ("wh/staging:a",),
    }


# ---------- multi-sink ----------


def test_fan_out_emits_edges_per_sink() -> None:
    cfg = _cfg(
        sink=None,
        sinks=[
            {"connection": "wh", "table": "a_only", "when": "data['type'] == 'a'"},
            {"connection": "wh", "table": "b_only", "when": "data['type'] == 'b'"},
        ],
    )
    out = _edge_map(derive_column_lineage(cfg))
    assert out == {
        "wh/a_only:a": ("wh/t1:a",),
        "wh/a_only:b": ("wh/t1:b",),
        "wh/b_only:a": ("wh/t1:a",),
        "wh/b_only:b": ("wh/t1:b",),
    }
