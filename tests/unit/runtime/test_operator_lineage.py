"""Catalog lineage for Operator DAG sql steps + the EXTRACT(... FROM ...) fix.

ADR-0099: a ``sql`` operator runs statements that write/read tables; those must
show in the catalog (a batch-log step's target was invisible before). Also a
regression guard for the mart-source key mis-deriving to ``TO_DATE`` because the
naive FROM regex matched ``EXTRACT(YEAR FROM TO_DATE(...))``.
"""

from __future__ import annotations

from etl_plugins.config.models import PipelineConfig
from etl_plugins.core.asset import derive_asset_key
from etl_plugins.core.sql_introspect import extract_statement_io
from etl_plugins.runtime.lineage import derive_lineage

# ---------------------------- extract_statement_io --------------------------


def test_insert_select_io() -> None:
    out, src = extract_statement_io("INSERT INTO mart.t (a, b) SELECT a, b FROM stg.s WHERE x > 1")
    assert out == ["mart.t"]
    assert src == ["stg.s"]


def test_delete_io() -> None:
    out, src = extract_statement_io("DELETE FROM mart.t WHERE day = '20260303'")
    assert out == ["mart.t"]
    assert src == []


def test_insert_constants_no_source() -> None:
    out, src = extract_statement_io("INSERT INTO log.t (a) SELECT 'x'")
    assert out == ["log.t"]
    assert src == []


def test_pure_select_is_not_a_write() -> None:
    assert extract_statement_io("SELECT * FROM s") == ([], ["s"])


def test_unparseable_is_empty() -> None:
    assert extract_statement_io("not really ((( sql") == ([], [])


# ---------------------- EXTRACT(... FROM ...) key fix ------------------------


def test_query_key_ignores_extract_from() -> None:
    # The naive regex used to grab TO_DATE from EXTRACT(YEAR FROM TO_DATE(...)).
    q = (
        "SELECT EXTRACT(YEAR FROM TO_DATE('20260303', 'YYYYMMDD')) AS y, COUNT(*) "
        "FROM bda_ds.fclts T1 GROUP BY 1"
    )
    key = derive_asset_key("c", {"query": q})
    assert key is not None
    assert str(key).endswith("/bda_ds.fclts")
    assert "TO_DATE" not in str(key)


# ------------------------- operator-DAG derive_lineage ----------------------


def test_sql_operator_target_is_an_output_asset() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                {
                    "name": "log",
                    "kind": "sql",
                    "connection": "wh",
                    "statements": ["INSERT INTO ops.batch_log (n) SELECT 1"],
                },
                {
                    "name": "load",
                    "depends_on": ["log"],
                    "source": {"connection": "wh", "query": "SELECT a FROM stg.s"},
                    "sink": {"connection": "wh", "table": "mart.t"},
                },
            ],
        }
    )
    lin = derive_lineage(cfg)
    out = {str(k) for k in lin.outputs}
    inp = {str(k) for k in lin.inputs}
    # sql step's target + etl sink are both outputs; etl source is an input.
    assert "wh/ops.batch_log" in out
    assert "wh/mart.t" in out
    assert "wh/stg.s" in inp
    assert any("TO_DATE" in s for s in out | inp) is False


def test_proc_call_is_opaque_no_lineage() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                {"name": "p1", "kind": "proc_call", "connection": "wh", "procedure": "X.Y"},
                {
                    "name": "load",
                    "depends_on": ["p1"],
                    "source": {"connection": "wh", "query": "SELECT a FROM stg.s"},
                    "sink": {"connection": "wh", "table": "mart.t"},
                },
            ],
        }
    )
    lin = derive_lineage(cfg)
    # proc_call contributes nothing; only the etl load's assets show.
    assert {str(k) for k in lin.outputs} == {"wh/mart.t"}


def test_proc_call_declared_lineage() -> None:
    """ADR-0099: proc_call is opaque, but declared reads/writes register in
    the catalog (Airflow-style inlets/outlets)."""
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                {
                    "name": "run_proc",
                    "kind": "proc_call",
                    "connection": "wh",
                    "procedure": "ops.daily_rollup",
                    "reads": ["stg.orders", "stg.customers"],
                    "writes": ["mart.daily"],
                }
            ],
        }
    )
    lin = derive_lineage(cfg)
    inp = {str(k) for k in lin.inputs}
    out = {str(k) for k in lin.outputs}
    assert inp == {"wh/stg.orders", "wh/stg.customers"}
    assert out == {"wh/mart.daily"}
    edges = {(str(e.upstream), str(e.downstream)) for e in lin.edges}
    assert ("wh/stg.orders", "wh/mart.daily") in edges
    assert ("wh/stg.customers", "wh/mart.daily") in edges


def test_proc_call_no_declaration_is_opaque() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                {"name": "p1", "kind": "proc_call", "connection": "wh", "procedure": "x.y"},
                {
                    "name": "load",
                    "depends_on": ["p1"],
                    "source": {"connection": "wh", "query": "SELECT a FROM stg.s"},
                    "sink": {"connection": "wh", "table": "mart.t"},
                },
            ],
        }
    )
    lin = derive_lineage(cfg)
    # undeclared proc contributes nothing.
    assert {str(k) for k in lin.outputs} == {"wh/mart.t"}
