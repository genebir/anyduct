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


def test_python_transform_without_mapping_emits_warning() -> None:
    cfg = _cfg(transforms=[{"type": "python", "callable": "m:f"}])
    warnings = lint_pipeline(cfg)
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
    warnings = lint_pipeline(cfg)
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
    assert lint_pipeline(cfg) == []


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
    warnings = lint_pipeline(cfg)
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
    warnings = lint_pipeline(cfg)
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
    warnings = lint_pipeline(cfg)
    assert len(warnings) == 1
    assert warnings[0].location == "graph.nodes.py"


def test_empty_pipeline_emits_no_warnings() -> None:
    """No transforms at all → nothing to lint."""
    assert lint_pipeline(_cfg()) == []
