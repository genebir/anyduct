"""Smoke-tests that the ``examples/*.yaml`` fixtures keep loading
cleanly (Phase AAH, 2026-05-29).

The cross-DB example doubles as user-facing documentation in
``CLAUDE.md`` / ADR-0072. If a future config-schema change breaks it,
the docs break too — better to catch that here than to ship a
copy-pasteable snippet that no longer parses.
"""

from __future__ import annotations

from pathlib import Path

from etl_plugins.config import load_pipeline

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"


def test_cross_db_migration_example_validates() -> None:
    pc = load_pipeline(EXAMPLES / "cross_db_migration.yaml")
    assert pc.name == "orders_replication"
    assert pc.mode == "batch"
    assert pc.sink is not None
    # The whole point of the example: auto_create_table + canonical
    # if_exists value land cleanly through the AAA→AAG narrowing.
    assert pc.sink.auto_create_table is True
    assert pc.sink.auto_create_if_exists == "skip"


def test_cross_db_snapshot_example_validates() -> None:
    """Phase AAM: snapshot rebuild example — ``drop`` if_exists +
    ``overwrite`` mode for daily schema-drift-tolerant replication."""
    pc = load_pipeline(EXAMPLES / "cross_db_snapshot.yaml")
    assert pc.name == "customers_snapshot"
    assert pc.sink is not None
    assert pc.sink.mode == "overwrite"
    assert pc.sink.auto_create_table is True
    assert pc.sink.auto_create_if_exists == "drop"


def test_cross_db_upsert_example_validates() -> None:
    """Phase AAM: live-cache UPSERT example — auto-emitted PRIMARY KEY
    from ``key_columns`` makes the first run safe (ADR-0072)."""
    pc = load_pipeline(EXAMPLES / "cross_db_upsert.yaml")
    assert pc.name == "customers_cache"
    assert pc.sink is not None
    assert pc.sink.mode == "upsert"
    assert pc.sink.key_columns == ["id"]
    assert pc.sink.auto_create_table is True


def test_cross_cloud_dw_migration_example_validates() -> None:
    """Phase AGE→AGH: cross-cloud DW migration (Snowflake → BigQuery)
    exercising the new connector types + canonical type translation."""
    pc = load_pipeline(EXAMPLES / "cross_cloud_dw_migration.yaml")
    assert pc.name == "warehouse_sync"
    assert pc.mode == "batch"
    assert pc.source is not None and pc.source.connection == "snowflake_dw"
    assert pc.sink is not None
    assert pc.sink.connection == "bigquery_dw"
    assert pc.sink.table == "analytics.events_mirror"
    assert pc.sink.mode == "upsert"
    assert pc.sink.key_columns == ["id"]
    assert pc.sink.auto_create_table is True


def test_stream_queue_to_stream_example_validates() -> None:
    """Phases AGM/AGN: stream-mode SQS → Redis Stream ingest with
    after_sink_flush commit (at-least-once)."""
    pc = load_pipeline(EXAMPLES / "stream_queue_to_stream.yaml")
    assert pc.name == "queue_to_stream"
    assert pc.mode == "stream"
    assert pc.source is not None and pc.source.connection == "sqs_jobs"
    assert pc.sink is not None and pc.sink.connection == "redis_events"
    assert pc.commit is not None
    assert pc.commit.strategy == "after_sink_flush"


def test_elt_pushdown_example_validates() -> None:
    """ADR-0094: in-warehouse ELT — same-connection + sql transform with
    ``pushdown: true`` composes into one INSERT INTO…WITH…SELECT. The
    example must stay pushdown-ELIGIBLE (lint-clean), or the docs would
    promise zero movement while the runtime quietly runs DuckDB."""
    from etl_plugins.runtime.lint import lint_pipeline

    pc = load_pipeline(EXAMPLES / "elt_pushdown.yaml")
    assert pc.name == "daily_revenue_rollup"
    assert pc.source is not None and pc.sink is not None
    assert pc.source.connection == pc.sink.connection
    assert len(pc.transforms) == 1
    assert pc.transforms[0].model_dump().get("pushdown") is True
    codes = {w.code for w in lint_pipeline(pc)}
    assert "sql_pushdown_ineligible" not in codes


def test_task_dag_batch_log_example_validates() -> None:
    """ADR-0028 + ADR-0035: layered load + batch-log audit row (the
    2026-06-12 live pattern). The DAG must keep its shape — parallel
    copy tasks (overwrite = idempotent) with the log task depending on
    ALL of them, appending on the same connection so it stays
    pushdown-eligible."""
    pc = load_pipeline(EXAMPLES / "task_dag_batch_log.yaml")
    assert pc.name == "staging_load_with_batch_log"
    assert len(pc.tasks) == 3
    by_name = {t.name: t for t in pc.tasks}
    log = by_name["write_batch_log"]
    assert set(log.depends_on) == {"load_customers", "load_orders"}
    assert log.effective_sinks()[0].mode == "append"
    assert log.source.connection == log.effective_sinks()[0].connection
    for t in ("load_customers", "load_orders"):
        sink = by_name[t].effective_sinks()[0]
        assert sink.mode == "overwrite"
        assert sink.auto_create_table is True


def test_operator_dag_mart_example_validates() -> None:
    """ADR-0099: operator DAG example — typed sql/etl steps, params, XCom,
    and an all_done error-log step. Doubles as docs for the operator model."""
    pc = load_pipeline(EXAMPLES / "operator_dag_mart.yaml")
    assert pc.name == "daily_sales_mart"
    assert pc.params == {"run_day": "20260601"}
    by_name = {t.name: t for t in pc.tasks}
    assert len(by_name) == 4
    # sql operator steps run statements against a connection (no source/sink).
    start = by_name["write_start_log"]
    assert start.kind == "sql"
    assert start.source is None and start.sink is None
    assert start.connection == "pg_oltp"
    assert len(start.statements) == 1
    # the etl load is the default kind with a pre_sql DELETE for idempotency.
    load = by_name["load_mart"]
    assert load.kind == "etl"
    assert load.source is not None
    assert load.depends_on == ["write_start_log"]
    # the end-log reads the load step's rows-affected via XCom.
    end = by_name["write_end_log"]
    assert "{{ xcom.load_mart.records_written }}" in end.statements[0]
    # the error-log runs even when an upstream step fails.
    assert by_name["write_error_log"].trigger_rule == "all_done"
