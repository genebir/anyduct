"""Runtime templating (``{{ }}``) — the per-run context layer.

Covers the safe path-only renderer + RuntimeContext mapping + config
recursion. The deliberate non-features (no code execution, no calls,
strict undefined) are asserted too, since they're the security contract.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from etl_plugins.core.exceptions import ConfigError
from etl_plugins.runtime.templating import (
    RuntimeContext,
    has_template,
    references_namespace,
    render_config_templates,
    render_templates,
    template_namespaces,
)

CTX = RuntimeContext(
    run_id="run-123",
    logical_date=datetime(2026, 6, 15, 13, 30, 0, tzinfo=UTC),
    params={"region": "kr", "limit": 100, "window": {"start": "2026-06-01"}},
    pipeline_name="orders",
).as_mapping()


# ---------- context mapping ----------


def test_context_mapping_keys() -> None:
    assert CTX["run_id"] == "run-123"
    assert CTX["ds"] == "2026-06-15"
    assert CTX["ds_nodash"] == "20260615"
    assert CTX["ts"].startswith("2026-06-15T13:30:00")
    assert CTX["pipeline_name"] == "orders"
    assert CTX["params"]["region"] == "kr"


# ---------- deferred namespaces (ADR-0097, xcom) ----------


def test_deferred_namespace_left_intact_whole_string() -> None:
    out = render_templates("{{ xcom.a.b }}", CTX, deferred=frozenset({"xcom"}))
    assert out == "{{ xcom.a.b }}"


def test_deferred_namespace_left_intact_embedded() -> None:
    out = render_templates(
        "WHERE id > {{ xcom.extract.new_cursor }} AND day = '{{ ds }}'",
        CTX,
        deferred=frozenset({"xcom"}),
    )
    # xcom kept verbatim, ds resolved
    assert out == "WHERE id > {{ xcom.extract.new_cursor }} AND day = '2026-06-15'"


def test_render_config_defers_xcom_by_default() -> None:
    ctx = RuntimeContext(
        run_id="r", logical_date=datetime(2026, 6, 15, tzinfo=UTC), pipeline_name="p"
    )
    cfg = {"query": "SELECT {{ ds }} {{ xcom.a.b }}"}
    out = render_config_templates(cfg, ctx)  # default deferred = {"xcom"}
    assert out["query"] == "SELECT 2026-06-15 {{ xcom.a.b }}"


def test_template_namespaces_collected() -> None:
    obj = {"q": "{{ ds }} {{ xcom.a.b }}", "t": ["{{ params.x }}"]}
    assert template_namespaces(obj) == {"ds", "xcom", "params"}


def test_references_namespace() -> None:
    assert references_namespace("{{ xcom.a.b }}", "xcom") is True
    assert references_namespace("{{ ds }}", "xcom") is False
    assert references_namespace({"k": ["{{ xcom.t.k }}"]}, "xcom") is True


# ---------- embedded interpolation ----------


def test_embedded_interpolation() -> None:
    out = render_templates("SELECT * FROM t WHERE day = '{{ ds }}'", CTX)
    assert out == "SELECT * FROM t WHERE day = '2026-06-15'"


def test_embedded_params_dotted() -> None:
    out = render_templates("WHERE region = '{{ params.region }}'", CTX)
    assert out == "WHERE region = 'kr'"


def test_nested_param_path() -> None:
    out = render_templates("since {{ params.window.start }}", CTX)
    assert out == "since 2026-06-01"


def test_multiple_refs_one_string() -> None:
    out = render_templates("{{ pipeline_name }}/{{ ds_nodash }}/{{ run_id }}", CTX)
    assert out == "orders/20260615/run-123"


def test_whitespace_inside_braces_ok() -> None:
    assert render_templates("{{ds}}", CTX) == "2026-06-15"
    assert render_templates("{{   ds   }}", CTX) == "2026-06-15"


# ---------- whole-string preserves type ----------


def test_whole_string_preserves_int() -> None:
    # ``chunk_size: "{{ params.limit }}"`` must stay an int, not "100".
    out = render_templates("{{ params.limit }}", CTX)
    assert out == 100 and isinstance(out, int)


def test_whole_string_preserves_dict() -> None:
    out = render_templates("{{ params.window }}", CTX)
    assert out == {"start": "2026-06-01"}


def test_embedded_int_becomes_str() -> None:
    out = render_templates("limit {{ params.limit }}", CTX)
    assert out == "limit 100"


# ---------- recursion over config shapes ----------


def test_render_nested_config() -> None:
    cfg = {
        "source": {"query": "SELECT * FROM e WHERE d='{{ ds }}'"},
        "sinks": [{"table": "out_{{ ds_nodash }}"}],
        "chunk_size": "{{ params.limit }}",
        "untouched": 7,
    }
    out = render_templates(cfg, CTX)
    assert out["source"]["query"] == "SELECT * FROM e WHERE d='2026-06-15'"
    assert out["sinks"][0]["table"] == "out_20260615"
    assert out["chunk_size"] == 100
    assert out["untouched"] == 7


# ---------- strict undefined ----------


def test_undefined_reference_raises() -> None:
    with pytest.raises(ConfigError, match="undefined"):
        render_templates("{{ params.missing }}", CTX)


def test_undefined_top_level_raises() -> None:
    with pytest.raises(ConfigError, match="undefined"):
        render_templates("{{ nope }}", CTX)


# ---------- security: no code execution ----------


def test_no_function_calls() -> None:
    # ``{{ ds }}`` style only — anything with ( ) or operators is NOT a
    # template ref and is left verbatim (no eval).
    s = "{{ __import__('os').system('x') }}"
    assert render_templates(s, CTX) == s  # not matched, untouched


def test_no_arithmetic_or_index() -> None:
    assert render_templates("{{ params.limit + 1 }}", CTX) == "{{ params.limit + 1 }}"
    assert render_templates("{{ params['region'] }}", CTX) == "{{ params['region'] }}"


# ---------- has_template / render_config_templates ----------


def test_has_template() -> None:
    assert has_template({"q": "x {{ ds }}"}) is True
    assert has_template({"q": "static", "n": [1, 2]}) is False


def test_render_config_templates_noop_when_no_templates() -> None:
    cfg = {"source": {"query": "SELECT 1"}}
    ctx = RuntimeContext(run_id="r", logical_date=datetime(2026, 1, 1, tzinfo=UTC))
    out = render_config_templates(cfg, ctx)
    assert out is cfg  # returned unchanged, no copy


def test_render_config_templates_applies() -> None:
    cfg = {"source": {"query": "d='{{ ds }}'"}}
    ctx = RuntimeContext(run_id="r", logical_date=datetime(2026, 1, 2, tzinfo=UTC))
    out = render_config_templates(cfg, ctx)
    assert out["source"]["query"] == "d='2026-01-02'"


def test_default_params_empty() -> None:
    ctx = RuntimeContext(run_id="r", logical_date=datetime(2026, 1, 1, tzinfo=UTC))
    assert ctx.as_mapping()["params"] == {}
