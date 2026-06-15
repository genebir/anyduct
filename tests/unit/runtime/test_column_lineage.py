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


def test_function_call_traces_through_argument() -> None:
    # Phase X (2026-05-28): the sqlglot.lineage walker traces *through*
    # function calls, so ``UPPER(b)`` now correctly attributes the output
    # column to ``t1.b``. The previous parser marked it opaque — that was a
    # known limitation we explicitly fixed.
    out = _edge_map(
        derive_column_lineage(
            _cfg(
                source={"connection": "wh", "query": "SELECT a, UPPER(b) AS bx FROM t1"},
            )
        )
    )
    assert out == {"wh/t2:a": ("wh/t1:a",), "wh/t2:bx": ("wh/t1:b",)}


def test_constant_only_expression_has_empty_upstream() -> None:
    # A literal projection has no upstream column at all — the row still
    # exists in the mapping (so the sink table has the column), but with an
    # empty upstream tuple.
    out = _edge_map(
        derive_column_lineage(
            _cfg(source={"connection": "wh", "query": "SELECT a, 42 AS answer FROM t1"})
        )
    )
    assert out == {"wh/t2:a": ("wh/t1:a",), "wh/t2:answer": ()}


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


def test_assert_transform_preserves_mapping() -> None:
    """ADR-0041 K1 — assert is a row-level gate (pass-through or fail/drop),
    so the column mapping is unchanged. Column lineage should resolve like
    a plain SELECT of the source's projected columns."""
    cfg = _cfg(
        source={"connection": "wh", "query": "SELECT id, amount FROM orders"},
        transforms=[{"type": "assert", "condition": "data['amount'] >= 0"}],
    )
    lineage = derive_column_lineage(cfg)
    edge_names = {(e.downstream.column, e.upstreams[0].column) for e in lineage.edges}
    assert edge_names == {("id", "id"), ("amount", "amount")}
    assert AssetKey.of("wh", "t2") not in lineage.opaque_assets


def test_custom_python_transform_marks_sink_opaque() -> None:
    """ADR-0041 I2 — inline custom_python user code can do anything to a
    record, so its downstream sink mapping is opaque (same posture as
    ``python``)."""
    cfg = _cfg(
        transforms=[
            {
                "type": "custom_python",
                "code": "def transform(record):\n    return record\n",
            }
        ]
    )
    lineage = derive_column_lineage(cfg)
    assert lineage.edges == []
    assert AssetKey.of("wh", "t2") in lineage.opaque_assets


def test_select_star_marks_sink_opaque() -> None:
    cfg = _cfg(source={"connection": "wh", "query": "SELECT * FROM t1"})
    assert _is_opaque(cfg, AssetKey.of("wh", "t2"))


def test_sql_join_resolves_per_table_upstreams() -> None:
    # Phase X (2026-05-28): joined queries used to mark the sink opaque
    # ("multi-source → give up"). The new sqlglot.lineage walker resolves
    # each output column to its originating table, so ``t1.a`` and ``t2.b``
    # land on the right asset keys via the source's connection.
    cfg = _cfg(
        source={
            "connection": "wh",
            "query": "SELECT t1.a, t2.b FROM t1 JOIN t2 ON t1.id = t2.id",
        },
        sink={"connection": "wh", "table": "joined"},
    )
    out = _edge_map(derive_column_lineage(cfg))
    assert out == {
        "wh/joined:a": ("wh/t1:a",),
        "wh/joined:b": ("wh/t2:b",),
    }


def test_sql_coalesce_join_emits_multi_source_upstreams() -> None:
    # COALESCE across a LEFT JOIN is the canonical case that *needs* the
    # tuple-of-upstreams shape — one output column with two source columns,
    # each on a different upstream asset.
    cfg = _cfg(
        source={
            "connection": "wh",
            "query": ("SELECT COALESCE(a.x, b.y) AS v FROM a LEFT JOIN b ON a.id = b.id"),
        },
        sink={"connection": "wh", "table": "merged"},
    )
    lineage = derive_column_lineage(cfg)
    [edge] = lineage.edges
    assert str(edge.downstream) == "wh/merged:v"
    assert {str(u) for u in edge.upstreams} == {"wh/a:x", "wh/b:y"}


# ---------- Phase CC: explicit column_mapping declaration ----------


def test_column_mapping_overrides_python_opaque() -> None:
    """Phase CC (ADR-0047, 2026-05-29): a ``column_mapping`` declaration on
    a python transform unlocks accurate lineage. Without it the python
    transform marks the sink opaque (and the worker's schema-passthrough
    fallback would guess by column name). With it, we honour the user's
    declared output → source attribution verbatim.
    """
    cfg = _cfg(
        transforms=[
            {
                "type": "custom_python",
                "code": "def transform(record):\n    return record\n",
                # User declares: ``email_normalized`` comes from ``a`` (source's
                # first column), ``tier`` is new (no upstream).
                "column_mapping": {
                    "email_normalized": ["a"],
                    "tier": [],
                },
            }
        ]
    )
    out = _edge_map(derive_column_lineage(cfg))
    assert out == {
        "wh/t2:email_normalized": ("wh/t1:a",),
        "wh/t2:tier": (),
    }
    # The sink is *not* opaque — accurate lineage means we don't need
    # the schema-passthrough fallback to kick in.
    assert AssetKey.of("wh", "t2") not in derive_column_lineage(cfg).opaque_assets


def test_column_mapping_multi_source_collapses_upstream_union() -> None:
    """An output column declared with multiple source columns becomes a
    multi-upstream ``ColumnEdge`` — every listed source's prior upstreams
    are unioned (dedup'd in first-seen order)."""
    cfg = _cfg(
        source={"connection": "wh", "query": "SELECT a, b FROM t1"},
        transforms=[
            {
                "type": "python",
                "callable": "m:f",
                "column_mapping": {
                    "full": ["a", "b"],  # full = a + b
                },
            }
        ],
    )
    lineage = derive_column_lineage(cfg)
    [edge] = lineage.edges
    assert str(edge.downstream) == "wh/t2:full"
    assert {str(u) for u in edge.upstreams} == {"wh/t1:a", "wh/t1:b"}


def test_column_mapping_is_replace_mode_not_merge() -> None:
    """Replace-mode determinism: only the columns the user declared end up
    in the final mapping. ``b`` was in the source but the user didn't
    name it, so it's intentionally not in the sink's column lineage."""
    cfg = _cfg(
        source={"connection": "wh", "query": "SELECT a, b FROM t1"},
        transforms=[
            {
                "type": "custom_python",
                "code": "def transform(record):\n    return record\n",
                "column_mapping": {"id": ["a"]},
            }
        ],
    )
    out = _edge_map(derive_column_lineage(cfg))
    assert out == {"wh/t2:id": ("wh/t1:a",)}


def test_column_mapping_unknown_source_column_yields_empty_upstream() -> None:
    """If the user names a source column that isn't in the current mapping
    (typo, renamed earlier in the chain), the output column exists but
    with no upstream — same shape as ``add_constant`` so the catalog
    still shows the column, just unattributed."""
    cfg = _cfg(
        transforms=[
            {
                "type": "custom_python",
                "code": "def transform(record):\n    return record\n",
                "column_mapping": {"x": ["does_not_exist"]},
            }
        ]
    )
    out = _edge_map(derive_column_lineage(cfg))
    assert out == {"wh/t2:x": ()}


def test_column_mapping_malformed_declaration_falls_through() -> None:
    """A malformed declaration (not a dict, or non-list values) should not
    corrupt the chain — fall back to the type-based handling (which for
    python means opaque)."""
    cfg = _cfg(
        transforms=[
            {
                "type": "custom_python",
                "code": "def transform(record):\n    return record\n",
                "column_mapping": "not-a-dict",
            }
        ]
    )
    lineage = derive_column_lineage(cfg)
    # The declaration was bad, so the chain proceeds. ``custom_python``
    # without a declaration is opaque → no edges, sink in opaque_assets.
    # (We tolerate the bad shape rather than crashing.)
    assert AssetKey.of("wh", "t2") in lineage.opaque_assets


def test_select_star_with_schema_resolves_columns() -> None:
    """Phase Z (2026-05-28): caller supplies a schemas map → ``SELECT *``
    expands per-column rather than marking the sink opaque. The schemas
    entry is the same ``{table: {column: type}}`` shape sqlglot's
    ``qualify`` expects, keyed by *connection name*."""
    cfg = _cfg(source={"connection": "wh", "query": "SELECT * FROM t1"})
    schemas = {"wh": {"t1": {"a": "INT", "b": "TEXT"}}}
    out = _edge_map(derive_column_lineage(cfg, schemas=schemas))
    assert out == {
        "wh/t2:a": ("wh/t1:a",),
        "wh/t2:b": ("wh/t1:b",),
    }


def test_select_star_without_schema_still_opaque() -> None:
    """Default (no schemas) keeps the v1 behaviour — star projections
    remain opaque so we never fabricate columns we can't see."""
    cfg = _cfg(source={"connection": "wh", "query": "SELECT * FROM t1"})
    lineage = derive_column_lineage(cfg)
    assert lineage.edges == []
    assert AssetKey.of("wh", "t2") in lineage.opaque_assets


def test_select_qualified_star_with_schema() -> None:
    """``t.*`` should expand the same way ``*`` does."""
    cfg = _cfg(source={"connection": "wh", "query": "SELECT t1.* FROM t1"})
    schemas = {"wh": {"t1": {"a": "INT", "b": "TEXT"}}}
    out = _edge_map(derive_column_lineage(cfg, schemas=schemas))
    assert out == {
        "wh/t2:a": ("wh/t1:a",),
        "wh/t2:b": ("wh/t1:b",),
    }


def test_sql_cte_chain_resolves_to_base_columns() -> None:
    # Chained CTEs were opaque before; now they resolve cleanly to the base
    # table columns at the bottom of the chain.
    cfg = _cfg(
        source={
            "connection": "wh",
            "query": """
            WITH a AS (SELECT id, name FROM users),
                 b AS (SELECT id, name FROM a WHERE id > 0)
            SELECT id AS uid, name AS nm FROM b
            """,
        },
        sink={"connection": "wh", "table": "out"},
    )
    out = _edge_map(derive_column_lineage(cfg))
    assert out == {
        "wh/out:uid": ("wh/users:id",),
        "wh/out:nm": ("wh/users:name",),
    }


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


def test_graph_join_unions_both_sides() -> None:
    """Fan-in join lineage (2026-06-12): the hash-join merges record
    dicts, so output columns = union of both inputs; the shared ``id``
    key traces to BOTH source tables. (v1 marked all of this opaque.)"""
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
    assert AssetKey.of("wh", "joined") not in lineage.opaque_assets
    out = _edge_map(lineage)
    assert set(out["wh/joined:id"]) == {"wh/t1:id", "wh/t2:id"}
    assert out["wh/joined:a"] == ("wh/t1:a",)
    assert out["wh/joined:b"] == ("wh/t2:b",)


def test_graph_multi_source_join_then_sql_transform() -> None:
    """The P1c shape: two sources → join → sql aggregate → sink. The
    catalog traces the aggregate output through the join to the right
    source table."""
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "graph": {
                "nodes": [
                    {
                        "id": "orders",
                        "type": "source",
                        "connection": "pg",
                        "query": "SELECT customer_id, amount FROM orders",
                    },
                    {
                        "id": "customers",
                        "type": "source",
                        "connection": "my",
                        "query": "SELECT customer_id, region FROM customers",
                    },
                    {"id": "j", "type": "join", "on": ["customer_id"]},
                    {
                        "id": "agg",
                        "type": "transform",
                        "transform": {
                            "type": "sql",
                            "query": (
                                "SELECT region, SUM(amount) AS total FROM input GROUP BY region"
                            ),
                        },
                    },
                    {"id": "k", "type": "sink", "connection": "pg", "table": "region_totals"},
                ],
                "edges": [
                    {"from_node": "orders", "to_node": "j"},
                    {"from_node": "customers", "to_node": "j"},
                    {"from_node": "j", "to_node": "agg"},
                    {"from_node": "agg", "to_node": "k"},
                ],
            },
        }
    )
    out = _edge_map(derive_column_lineage(cfg))
    assert out == {
        "pg/region_totals:region": ("my/customers:region",),
        "pg/region_totals:total": ("pg/orders:amount",),
    }


def test_graph_aggregate_node_reshapes_columns() -> None:
    """Aggregate node lineage (2026-06-12): output = group keys + agg
    names. (The v1 walker silently skipped aggregate nodes, leaving the
    pre-aggregation column set on the sink — wrong columns.)"""
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "graph": {
                "nodes": [
                    {
                        "id": "s",
                        "type": "source",
                        "connection": "wh",
                        "query": "SELECT region, amount FROM sales",
                    },
                    {
                        "id": "g",
                        "type": "aggregate",
                        "group_by": ["region"],
                        "aggregations": [
                            {"op": "sum", "column": "amount", "name": "total"},
                            {"op": "count", "name": "n"},
                        ],
                    },
                    {"id": "k", "type": "sink", "connection": "wh", "table": "rollup"},
                ],
                "edges": [
                    {"from_node": "s", "to_node": "g"},
                    {"from_node": "g", "to_node": "k"},
                ],
            },
        }
    )
    out = _edge_map(derive_column_lineage(cfg))
    assert out == {
        "wh/rollup:region": ("wh/sales:region",),
        "wh/rollup:total": ("wh/sales:amount",),
        "wh/rollup:n": (),
    }


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


# ---------- sql dataset transform (ADR-0093) — sqlglot-inferred lineage ----


def test_sql_transform_aggregate_traces_to_source_columns() -> None:
    """SUM(b) GROUP BY a — both outputs trace through the in-flight view
    back to the source table, no manual column_mapping needed."""
    out = _edge_map(
        derive_column_lineage(
            _cfg(
                transforms=[
                    {
                        "type": "sql",
                        "query": "SELECT a, SUM(b) AS total FROM input GROUP BY a",
                    }
                ]
            )
        )
    )
    assert out == {
        "wh/t2:a": ("wh/t1:a",),
        "wh/t2:total": ("wh/t1:b",),
    }


def test_sql_transform_rename_and_combine() -> None:
    """a AS key + (a || b) AS combo — multi-upstream union survives."""
    out = _edge_map(
        derive_column_lineage(
            _cfg(
                transforms=[
                    {
                        "type": "sql",
                        "query": "SELECT a AS key, a || b AS combo FROM input",
                    }
                ]
            )
        )
    )
    assert out["wh/t2:key"] == ("wh/t1:a",)
    assert set(out["wh/t2:combo"]) == {"wh/t1:a", "wh/t1:b"}


def test_sql_transform_custom_view_name() -> None:
    out = _edge_map(
        derive_column_lineage(
            _cfg(
                transforms=[
                    {
                        "type": "sql",
                        "query": "SELECT a FROM rows_in",
                        "view": "rows_in",
                    }
                ]
            )
        )
    )
    assert out == {"wh/t2:a": ("wh/t1:a",)}


def test_sql_transform_select_star_expands_via_view_schema() -> None:
    """The view's columns ARE the upstream mapping keys — star expands."""
    out = _edge_map(
        derive_column_lineage(_cfg(transforms=[{"type": "sql", "query": "SELECT * FROM input"}]))
    )
    assert out == {
        "wh/t2:a": ("wh/t1:a",),
        "wh/t2:b": ("wh/t1:b",),
    }


def test_sql_transform_constant_column_has_empty_upstream() -> None:
    out = _edge_map(
        derive_column_lineage(
            _cfg(transforms=[{"type": "sql", "query": "SELECT a, 'x' AS tag FROM input"}])
        )
    )
    assert out["wh/t2:a"] == ("wh/t1:a",)
    assert out["wh/t2:tag"] == ()


def test_sql_transform_chains_with_row_transforms() -> None:
    """rename → sql — the sql stage sees the RENAMED column names."""
    out = _edge_map(
        derive_column_lineage(
            _cfg(
                transforms=[
                    {"type": "rename", "mapping": {"a": "id"}},
                    {
                        "type": "sql",
                        "query": "SELECT id, COUNT(*) AS n FROM input GROUP BY id",
                    },
                ]
            )
        )
    )
    assert out["wh/t2:id"] == ("wh/t1:a",)


def test_sql_transform_unparseable_query_marks_opaque() -> None:
    cfg = _cfg(transforms=[{"type": "sql", "query": "NOT REALLY ((( SQL"}])
    assert _is_opaque(cfg, AssetKey.of("wh", "t2"))


def test_sql_transform_explicit_column_mapping_still_wins() -> None:
    """Phase CC declaration overrides the sqlglot inference (user ground
    truth beats heuristics — same precedence as every other type)."""
    out = _edge_map(
        derive_column_lineage(
            _cfg(
                transforms=[
                    {
                        "type": "sql",
                        "query": "SELECT a AS weird FROM input",
                        "column_mapping": {"weird": ["b"]},
                    }
                ]
            )
        )
    )
    assert out == {"wh/t2:weird": ("wh/t1:b",)}


def test_graph_sql_transform_node_infers_lineage() -> None:
    """The builder saves pipelines as graphs — the sql transform node's
    sqlglot inference must work there too (same _apply_transform)."""
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
                        "id": "x",
                        "type": "transform",
                        "transform": {
                            "type": "sql",
                            "query": "SELECT a, SUM(b) AS total FROM input GROUP BY a",
                        },
                    },
                    {"id": "k", "type": "sink", "connection": "wh", "table": "t2"},
                ],
                "edges": [
                    {"from_node": "s", "to_node": "x"},
                    {"from_node": "x", "to_node": "k"},
                ],
            },
        }
    )
    out = _edge_map(derive_column_lineage(cfg))
    assert out == {"wh/t2:a": ("wh/t1:a",), "wh/t2:total": ("wh/t1:b",)}
