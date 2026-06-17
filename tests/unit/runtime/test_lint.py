"""Pipeline lint rules (Phase DD, 2026-05-29)."""

from __future__ import annotations

from etl_plugins.config.models import PipelineConfig
from etl_plugins.runtime.lint import lint_pipeline


def _cfg(**over: object) -> PipelineConfig:
    """Minimal pipeline config with single-task source/sink."""
    base: dict = {
        "name": "p",
        "source": {"connection": "wh", "query": "SELECT a, b FROM t1"},
        "sink": {"connection": "wh", "table": "t2"},
    }
    base.update(over)
    return PipelineConfig.model_validate(base)


# ---------- column_mapping_recommended ----------


def _mapping_warnings(cfg: PipelineConfig) -> list:
    """Only the ``column_mapping_recommended`` warnings — keeps these
    tests focused now that ``dlq_recommended`` (ADR-0076) also fires for
    python/custom_python transforms without a DLQ."""
    return [w for w in lint_pipeline(cfg) if w.code == "column_mapping_recommended"]


def test_python_transform_without_mapping_emits_warning() -> None:
    cfg = _cfg(transforms=[{"type": "python", "callable": "m:f"}])
    warnings = _mapping_warnings(cfg)
    assert len(warnings) == 1
    assert warnings[0].code == "column_mapping_recommended"
    assert warnings[0].location == "transforms.0"
    assert "python" in warnings[0].message


def test_custom_python_without_mapping_emits_warning() -> None:
    cfg = _cfg(
        transforms=[
            {
                "type": "custom_python",
                "code": "def transform(record):\n    return record\n",
            }
        ]
    )
    warnings = _mapping_warnings(cfg)
    assert len(warnings) == 1
    assert warnings[0].code == "column_mapping_recommended"


def test_sql_exec_without_mapping_emits_warning() -> None:
    cfg = _cfg(transforms=[{"type": "sql_exec", "connection": "wh", "statement": "VACUUM t1"}])
    warnings = lint_pipeline(cfg)
    assert len(warnings) == 1
    assert warnings[0].code == "column_mapping_recommended"


def test_python_transform_with_mapping_emits_no_warning() -> None:
    """Phase CC: user declared explicit column_mapping → no nudge needed."""
    cfg = _cfg(
        transforms=[
            {
                "type": "custom_python",
                "code": "def transform(record):\n    return record\n",
                "column_mapping": {"a": ["a"], "b": ["b"]},
            }
        ]
    )
    assert _mapping_warnings(cfg) == []


def test_declarative_transforms_emit_no_warning() -> None:
    """Static-analysable transforms (rename/cast/select/drop/filter/etc.)
    already produce accurate column lineage — no nudge."""
    cfg = _cfg(
        transforms=[
            {"type": "rename", "mapping": {"a": "id"}},
            {"type": "cast", "columns": {"id": "int"}},
            {"type": "select", "columns": ["id", "b"]},
            {"type": "filter", "expr": "True"},
        ]
    )
    assert lint_pipeline(cfg) == []


def test_multiple_opaque_transforms_emit_multiple_warnings() -> None:
    """The rule fires once per opaque transform — useful for chained
    python steps where the user might want a mapping on each."""
    cfg = _cfg(
        transforms=[
            {"type": "python", "callable": "m:a"},
            {"type": "rename", "mapping": {"a": "id"}},
            {"type": "custom_python", "code": "def transform(r):\n    return r\n"},
        ]
    )
    warnings = _mapping_warnings(cfg)
    assert len(warnings) == 2
    assert {w.location for w in warnings} == {"transforms.0", "transforms.2"}


# ---------- shape coverage ----------


def test_task_dag_shape_walks_every_task() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                {
                    "name": "t1",
                    "source": {"connection": "wh", "query": "SELECT a FROM raw"},
                    "sink": {"connection": "wh", "table": "staging"},
                    "transforms": [{"type": "python", "callable": "m:f"}],
                },
                {
                    "name": "t2",
                    "source": {"connection": "wh", "query": "SELECT a FROM staging"},
                    "sink": {"connection": "wh", "table": "mart"},
                    "depends_on": ["t1"],
                    "transforms": [{"type": "rename", "mapping": {"a": "id"}}],
                },
            ],
        }
    )
    warnings = _mapping_warnings(cfg)
    assert len(warnings) == 1
    assert warnings[0].location == "tasks.0.transforms.0"


def test_graph_shape_walks_every_transform_node() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "graph": {
                "nodes": [
                    {
                        "id": "s",
                        "type": "source",
                        "connection": "wh",
                        "query": "SELECT a FROM t",
                    },
                    {
                        "id": "py",
                        "type": "transform",
                        "transform": {
                            "type": "custom_python",
                            "code": "def transform(r):\n    return r\n",
                        },
                    },
                    {"id": "k", "type": "sink", "connection": "wh", "table": "out"},
                ],
                "edges": [
                    {"from_node": "s", "to_node": "py"},
                    {"from_node": "py", "to_node": "k"},
                ],
            },
        }
    )
    warnings = _mapping_warnings(cfg)
    assert len(warnings) == 1
    assert warnings[0].location == "graph.nodes.py"


def test_empty_pipeline_emits_no_warnings() -> None:
    """No transforms at all → nothing to lint."""
    assert lint_pipeline(_cfg()) == []


# ---------- Phase FF: column_mapping consistency ----------


def test_column_mapping_unknown_source_column_emits_warning() -> None:
    """Phase FF (ADR-0050): if the user names a source column that isn't
    actually in the upstream mapping (typo, or already renamed away by an
    earlier transform), surface it. The catalog would otherwise emit an
    empty-upstream row for the output — silently losing the user's
    declared intent."""
    cfg = _cfg(
        # Source projects {a, b}. The mapping for 'wh/t1' will have keys 'a', 'b'.
        source={"connection": "wh", "query": "SELECT a, b FROM t1"},
        transforms=[
            {
                "type": "custom_python",
                "code": "def transform(record):\n    return record\n",
                "column_mapping": {
                    "id": ["a"],
                    "wrong_name": ["doesnt_exist"],  # typo
                },
            }
        ],
    )
    warnings = lint_pipeline(cfg)
    typo_warnings = [w for w in warnings if w.code == "column_mapping_unknown_source_column"]
    assert len(typo_warnings) == 1
    assert "doesnt_exist" in typo_warnings[0].message
    assert "wrong_name" in typo_warnings[0].message
    assert typo_warnings[0].location == "transforms.0"


def test_column_mapping_with_valid_source_columns_passes() -> None:
    """Declaration that names real source columns → no typo warning."""
    cfg = _cfg(
        transforms=[
            {
                "type": "custom_python",
                "code": "def transform(record):\n    return record\n",
                "column_mapping": {"out_a": ["a"], "out_b": ["b"]},
            }
        ]
    )
    warnings = lint_pipeline(cfg)
    assert not any(w.code == "column_mapping_unknown_source_column" for w in warnings)


def test_column_mapping_consistency_walks_through_rename() -> None:
    """A rename earlier in the chain renames ``a`` → ``id``. A later
    column_mapping referencing ``a`` should fire the typo lint (the
    column lineage walker has already moved on to ``id``)."""
    cfg = _cfg(
        transforms=[
            {"type": "rename", "mapping": {"a": "id"}},
            {
                "type": "custom_python",
                "code": "def transform(r):\n    return r\n",
                "column_mapping": {"out": ["a"]},  # ``a`` no longer exists
            },
        ]
    )
    typos = [w for w in lint_pipeline(cfg) if w.code == "column_mapping_unknown_source_column"]
    assert len(typos) == 1
    assert typos[0].location == "transforms.1"


def test_column_mapping_with_new_column_empty_list_no_warning() -> None:
    """``output: []`` means "this is a new column" — no source col to
    validate, so no typo lint."""
    cfg = _cfg(
        transforms=[
            {
                "type": "custom_python",
                "code": "def transform(r):\n    return r\n",
                "column_mapping": {"tier": []},  # new column, no upstream
            }
        ]
    )
    warnings = lint_pipeline(cfg)
    assert not any(w.code == "column_mapping_unknown_source_column" for w in warnings)


def test_column_mapping_consistency_skips_graph_shape() -> None:
    """Graph-shape lint coverage is a separate slice — current consistency
    walker bails (no false positives, no coverage either)."""
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "source", "connection": "wh", "query": "SELECT a FROM t"},
                    {
                        "id": "py",
                        "type": "transform",
                        "transform": {
                            "type": "custom_python",
                            "code": "def transform(r):\n    return r\n",
                            "column_mapping": {"x": ["nope"]},
                        },
                    },
                    {"id": "k", "type": "sink", "connection": "wh", "table": "out"},
                ],
                "edges": [
                    {"from_node": "s", "to_node": "py"},
                    {"from_node": "py", "to_node": "k"},
                ],
            },
        }
    )
    typos = [w for w in lint_pipeline(cfg) if w.code == "column_mapping_unknown_source_column"]
    assert typos == []


# ---------- auto_create_table_planned (Phase AAK, 2026-05-29) ----------


def _planned(cfg: PipelineConfig) -> list[object]:
    return [w for w in lint_pipeline(cfg) if w.code == "auto_create_table_planned"]


def test_auto_create_table_off_emits_no_warning() -> None:
    cfg = _cfg()
    assert _planned(cfg) == []


def test_auto_create_table_on_with_skip_emits_friendly_message() -> None:
    cfg = _cfg(sink={"connection": "wh", "table": "t2", "auto_create_table": True})
    warnings = _planned(cfg)
    assert len(warnings) == 1
    w = warnings[0]
    assert "first run" in w.message  # type: ignore[attr-defined]
    assert "'t2'" in w.message  # type: ignore[attr-defined]
    assert w.location == "sink"  # type: ignore[attr-defined]


def test_auto_create_table_on_with_drop_warns_about_rebuild() -> None:
    cfg = _cfg(
        sink={
            "connection": "wh",
            "table": "t2",
            "auto_create_table": True,
            "auto_create_if_exists": "drop",
        }
    )
    warnings = _planned(cfg)
    assert len(warnings) == 1
    assert "rebuild" in warnings[0].message  # type: ignore[attr-defined]


def test_auto_create_table_fanout_warns_per_sink() -> None:
    cfg = _cfg(
        sink=None,
        sinks=[
            {"connection": "wh", "table": "a", "auto_create_table": True},
            {"connection": "wh", "table": "b"},
            {"connection": "wh", "table": "c", "auto_create_table": True},
        ],
    )
    warnings = _planned(cfg)
    # Two of the three sinks opted in — one warning each.
    assert len(warnings) == 2
    locations = {w.location for w in warnings}  # type: ignore[attr-defined]
    assert locations == {"sinks.0", "sinks.2"}


def test_auto_create_table_graph_shape_warns_at_node() -> None:
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
                        "id": "k",
                        "type": "sink",
                        "connection": "wh",
                        "table": "out",
                        "auto_create_table": True,
                        "auto_create_if_exists": "drop",
                    },
                ],
                "edges": [{"from_node": "s", "to_node": "k"}],
            },
        }
    )
    warnings = _planned(cfg)
    assert len(warnings) == 1
    assert warnings[0].location == "graph.nodes.k"  # type: ignore[attr-defined]
    assert "rebuild" in warnings[0].message  # type: ignore[attr-defined]


# ---------- dlq_recommended (Phase DLQ-8, ADR-0076) ----------


def _dlq(cfg: PipelineConfig) -> list:
    return [w for w in lint_pipeline(cfg) if w.code == "dlq_recommended"]


def test_dlq_recommended_fires_for_custom_python_without_dlq() -> None:
    cfg = _cfg(transforms=[{"type": "custom_python", "code": "def transform(r):\n    return r\n"}])
    warnings = _dlq(cfg)
    assert len(warnings) == 1
    assert warnings[0].location is None
    assert "dlq" in warnings[0].message.lower()


def test_dlq_recommended_silent_when_dlq_configured() -> None:
    cfg = _cfg(
        transforms=[{"type": "python", "callable": "m:f"}],
        dlq={"connection": "wh", "table": "bad", "mode": "append"},
    )
    assert _dlq(cfg) == []


def test_dlq_recommended_silent_for_declarative_only() -> None:
    """rename/cast/etc. don't run per-record code → no run-failure risk."""
    cfg = _cfg(transforms=[{"type": "rename", "mapping": {"a": "id"}}])
    assert _dlq(cfg) == []


def test_dlq_recommended_silent_for_sql_exec() -> None:
    """``sql_exec`` is a one-shot statement, not per-record — a DLQ
    (record-level routing) wouldn't help, so no nudge."""
    cfg = _cfg(transforms=[{"type": "sql_exec", "connection": "wh", "statement": "VACUUM t1"}])
    assert _dlq(cfg) == []


def test_dlq_recommended_fires_once_for_multiple_code_transforms() -> None:
    cfg = _cfg(
        transforms=[
            {"type": "python", "callable": "m:a"},
            {"type": "custom_python", "code": "def transform(r):\n    return r\n"},
        ]
    )
    assert len(_dlq(cfg)) == 1  # pipeline-level, fires once


def test_dlq_recommended_fires_in_graph_shape() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "source", "connection": "wh", "query": "SELECT a FROM t"},
                    {
                        "id": "py",
                        "type": "transform",
                        "transform": {
                            "type": "custom_python",
                            "code": "def transform(r):\n    return r\n",
                        },
                    },
                    {"id": "k", "type": "sink", "connection": "wh", "table": "out"},
                ],
                "edges": [
                    {"from_node": "s", "to_node": "py"},
                    {"from_node": "py", "to_node": "k"},
                ],
            },
        }
    )
    assert len(_dlq(cfg)) == 1


# ---------- sql_pushdown_ineligible (ADR-0094) ----------


def _pd(cfg: PipelineConfig) -> list:
    return [w for w in lint_pipeline(cfg) if w.code == "sql_pushdown_ineligible"]


def _sql_pd(**extra: object) -> dict:
    return {"type": "sql", "query": "SELECT a FROM input", "pushdown": True, **extra}


def test_pushdown_eligible_shape_emits_no_warning() -> None:
    cfg = _cfg(transforms=[_sql_pd()])
    assert _pd(cfg) == []


def test_pushdown_without_flag_emits_no_warning() -> None:
    cfg = _cfg(transforms=[{"type": "sql", "query": "SELECT a FROM input"}])
    assert _pd(cfg) == []


def test_pushdown_cross_connection_warns() -> None:
    cfg = _cfg(
        transforms=[_sql_pd()],
        sink={"connection": "other", "table": "t2"},
    )
    warnings = _pd(cfg)
    assert len(warnings) == 1
    assert "differ" in warnings[0].message
    assert warnings[0].location == "transforms.0"


def test_pushdown_non_append_mode_warns() -> None:
    cfg = _cfg(transforms=[_sql_pd()], sink={"connection": "wh", "table": "t2", "mode": "upsert"})
    warnings = _pd(cfg)
    assert len(warnings) == 1
    assert "'append'" in warnings[0].message


def test_pushdown_extra_transform_warns() -> None:
    cfg = _cfg(transforms=[{"type": "rename", "mapping": {}}, _sql_pd()])
    warnings = _pd(cfg)
    assert len(warnings) == 1
    assert "ONLY transform" in warnings[0].message
    assert warnings[0].location == "transforms.1"


def test_pushdown_sink_pre_sql_warns() -> None:
    cfg = _cfg(
        transforms=[_sql_pd()],
        sink={"connection": "wh", "table": "t2", "pre_sql": "DELETE FROM t2"},
    )
    warnings = _pd(cfg)
    assert len(warnings) == 1
    assert "pre_sql" in warnings[0].message


def test_pushdown_fancy_table_identifier_warns() -> None:
    cfg = _cfg(transforms=[_sql_pd()], sink={"connection": "wh", "table": 'we"ird'})
    warnings = _pd(cfg)
    assert len(warnings) == 1
    assert "plain identifier" in warnings[0].message


def test_pushdown_task_shape_location() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                {
                    "name": "t1",
                    "source": {"connection": "wh", "query": "SELECT a FROM t"},
                    "transforms": [_sql_pd()],
                    "sink": {"connection": "wh", "table": "out", "mode": "overwrite"},
                }
            ],
        }
    )
    warnings = _pd(cfg)
    assert len(warnings) == 1
    assert warnings[0].location == "tasks.0.transforms.0"


def _graph_cfg(
    *,
    sink: dict | None = None,
    extra_nodes: list[dict] | None = None,
    extra_edges: list[dict] | None = None,
) -> PipelineConfig:
    """Trivial source → sql(pushdown) → sink chain, optionally perturbed."""
    return PipelineConfig.model_validate(
        {
            "name": "p",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "source", "connection": "wh", "query": "SELECT a FROM t"},
                    {"id": "x", "type": "transform", "transform": _sql_pd()},
                    sink or {"id": "k", "type": "sink", "connection": "wh", "table": "out"},
                    *(extra_nodes or []),
                ],
                "edges": [
                    {"from_node": "s", "to_node": "x"},
                    {"from_node": "x", "to_node": "k"},
                    *(extra_edges or []),
                ],
            },
        }
    )


def test_pushdown_graph_trivial_chain_is_eligible() -> None:
    """The builder UI emits graphs — the simple chain must lint clean
    (the runtime composes it via ``_try_graph_fast_paths``, ADR-0094)."""
    assert _pd(_graph_cfg()) == []


def test_pushdown_graph_cross_connection_warns() -> None:
    cfg = _graph_cfg(sink={"id": "k", "type": "sink", "connection": "other", "table": "out"})
    warnings = _pd(cfg)
    assert len(warnings) == 1
    assert warnings[0].location == "graph.nodes.x"
    assert "differ" in warnings[0].message


def test_pushdown_graph_non_append_warns() -> None:
    cfg = _graph_cfg(
        sink={"id": "k", "type": "sink", "connection": "wh", "table": "out", "mode": "overwrite"}
    )
    warnings = _pd(cfg)
    assert len(warnings) == 1
    assert "'append'" in warnings[0].message


def test_pushdown_graph_non_trivial_shape_warns() -> None:
    cfg = _graph_cfg(
        extra_nodes=[
            {"id": "k2", "type": "sink", "connection": "wh", "table": "out2"},
        ],
        extra_edges=[{"from_node": "x", "to_node": "k2"}],
    )
    warnings = _pd(cfg)
    assert len(warnings) == 1
    assert "chain" in warnings[0].message


# ---------- sql transform: nudge only when sqlglot can't analyse ----------


def test_sql_transform_analysable_emits_no_mapping_nudge() -> None:
    """ADR-0093 f/u: the sqlglot walker infers lineage for sql transforms
    automatically — recommending a manual column_mapping is noise."""
    cfg = _cfg(
        transforms=[{"type": "sql", "query": "SELECT a, SUM(b) AS total FROM input GROUP BY a"}]
    )
    assert _mapping_warnings(cfg) == []


def test_sql_transform_unparseable_still_nudges() -> None:
    cfg = _cfg(transforms=[{"type": "sql", "query": "NOT REALLY ((( SQL"}])
    warnings = _mapping_warnings(cfg)
    assert len(warnings) == 1


# ---------- deferred template refs: map / xcom (ADR-0097/0098) ----------


def _codes(cfg: PipelineConfig, *prefixes: str) -> list[str]:
    """Codes of warnings whose code starts with any given prefix."""
    return [
        w.code
        for w in lint_pipeline(cfg)
        if not prefixes or any(w.code.startswith(p) for p in prefixes)
    ]


def _task(name: str, query: str, table: str = "out", **over: object) -> dict:
    base: dict = {
        "name": name,
        "source": {"connection": "wh", "query": query},
        "sink": {"connection": "wh", "table": table},
    }
    base.update(over)
    return base


def test_map_ref_without_expand_warns() -> None:
    cfg = PipelineConfig.model_validate(
        {"name": "p", "tasks": [_task("load", "SELECT * FROM t WHERE r = '{{ map.region }}'")]}
    )
    assert _codes(cfg, "map_") == ["map_ref_without_expand"]


def test_map_ref_with_matching_expand_key_is_clean() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                _task(
                    "load",
                    "SELECT * FROM t WHERE r = '{{ map.region }}'",
                    expand={"region": ["us", "eu"]},
                )
            ],
        }
    )
    assert _codes(cfg, "map_") == []


def test_map_ref_unknown_key_warns() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                _task(
                    "load",
                    "SELECT * FROM t WHERE r = '{{ map.region }}'",
                    expand={"shard": [1, 2]},
                )
            ],
        }
    )
    assert _codes(cfg, "map_") == ["map_ref_unknown_key"]


def test_xcom_ref_unknown_task_warns() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                _task("a", "SELECT 1"),
                _task("b", "SELECT * WHERE id > {{ xcom.nope.new_cursor }}", depends_on=["a"]),
            ],
        }
    )
    assert _codes(cfg, "xcom_") == ["xcom_ref_unknown_task"]


def test_xcom_ref_upstream_dependency_is_clean() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                _task("a", "SELECT 1"),
                _task("b", "SELECT * WHERE id > {{ xcom.a.new_cursor }}", depends_on=["a"]),
            ],
        }
    )
    assert _codes(cfg, "xcom_") == []


def test_xcom_ref_transitive_upstream_is_clean() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                _task("a", "SELECT 1"),
                _task("b", "SELECT 2", depends_on=["a"]),
                _task("c", "SELECT * WHERE id > {{ xcom.a.new_cursor }}", depends_on=["b"]),
            ],
        }
    )
    assert _codes(cfg, "xcom_") == []


def test_xcom_ref_not_upstream_warns() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                _task("a", "SELECT 1"),
                _task("b", "SELECT * WHERE id > {{ xcom.a.new_cursor }}"),
            ],
        }
    )
    assert _codes(cfg, "xcom_") == ["xcom_ref_not_upstream"]


def test_map_in_sink_table_is_scanned() -> None:
    cfg = PipelineConfig.model_validate(
        {"name": "p", "tasks": [_task("load", "SELECT 1", table="out_{{ map.shard }}")]}
    )
    assert _codes(cfg, "map_") == ["map_ref_without_expand"]


def test_single_task_shape_emits_no_deferred_warnings() -> None:
    """Legacy single-task shape has no other tasks / no expand field — the
    rule is task-DAG only and must not fire here."""
    cfg = _cfg(source={"connection": "wh", "query": "SELECT * WHERE r = '{{ map.region }}'"})
    assert _codes(cfg, "map_", "xcom_") == []
