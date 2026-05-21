"""Pipeline variable substitution — ``${var.name}`` (ADR-0041, V1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from etl_plugins.config.loader import load_pipeline
from etl_plugins.config.models import PipelineConfig
from etl_plugins.config.variables import resolve_config_variables, resolve_variables
from etl_plugins.core.exceptions import ConfigError


def test_whole_string_preserves_type() -> None:
    assert resolve_variables("${var.cs}", {"cs": 5000}) == 5000
    assert resolve_variables("${var.flag}", {"flag": True}) is True
    assert resolve_variables("${var.items}", {"items": [1, 2]}) == [1, 2]


def test_embedded_string_interpolation() -> None:
    out = resolve_variables(
        "SELECT * FROM t WHERE id > ${var.min_id} AND env = '${var.env}'",
        {"min_id": 100, "env": "prod"},
    )
    assert out == "SELECT * FROM t WHERE id > 100 AND env = 'prod'"


def test_nested_dict_and_list() -> None:
    obj = {"a": ["${var.x}", "k=${var.x}"], "b": {"c": "${var.y}"}}
    assert resolve_variables(obj, {"x": 1, "y": "z"}) == {"a": [1, "k=1"], "b": {"c": "z"}}


def test_undefined_variable_raises() -> None:
    with pytest.raises(ConfigError, match=r"var\.missing"):
        resolve_variables("${var.missing}", {})


def test_env_namespace_untouched() -> None:
    # ``${UPPER}`` is the env namespace (config.loader), not a variable ref.
    assert resolve_variables("${HOME}/x", {"HOME": "ignored"}) == "${HOME}/x"


def test_resolve_config_uses_local_block_and_skips_it() -> None:
    config = {
        "name": "p",
        "variables": {"tbl": "orders", "min": 10},
        "source": {"connection": "db", "query": "SELECT * FROM ${var.tbl} WHERE id > ${var.min}"},
    }
    out = resolve_config_variables(config)
    assert out["source"]["query"] == "SELECT * FROM orders WHERE id > 10"
    # the variables block itself is left intact (no inter-variable refs in V1).
    assert out["variables"] == {"tbl": "orders", "min": 10}


def test_local_overrides_extra_globals() -> None:
    config = {"name": "p", "variables": {"env": "local"}, "x": "${var.env}"}
    out = resolve_config_variables(config, extra={"env": "global", "region": "kr"})
    assert out["x"] == "local"  # local wins
    # global-only var still resolvable
    out2 = resolve_config_variables({"name": "p", "x": "${var.region}"}, extra={"region": "kr"})
    assert out2["x"] == "kr"


def test_load_pipeline_resolves_variables(tmp_path: Path) -> None:
    path = tmp_path / "pipe.yaml"
    path.write_text(
        "name: p\n"
        "variables:\n"
        "  tbl: orders\n"
        "source:\n"
        "  connection: db\n"
        "  query: SELECT * FROM ${var.tbl}\n"
        "sink:\n"
        "  connection: wh\n"
        "  table: out\n",
        encoding="utf-8",
    )
    cfg = load_pipeline(str(path))
    assert cfg.source is not None
    assert cfg.source.query == "SELECT * FROM orders"
    assert cfg.variables == {"tbl": "orders"}


def test_pipeline_config_carries_variables() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "variables": {"a": 1},
            "source": {"connection": "s"},
            "sink": {"connection": "k", "table": "o"},
        }
    )
    assert cfg.variables == {"a": 1}
