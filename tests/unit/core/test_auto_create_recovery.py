"""``_auto_create_sink_tables`` failure recovery (Phase AAR, 2026-06-01).

The user-visible symptom that led to this file:

  ``postgres write failed: current transaction is aborted, commands
   ignored until end of transaction block``

Root cause: a previous ``contextlib.suppress`` swallowed a CREATE
TABLE failure (e.g. schema didn't exist on the destination) but
left the connection's transaction in *aborted* state, so the
subsequent ``sink.write`` couldn't run a single statement.

These tests pin down the fixed behaviour:

* The pipeline still runs to completion when ``ensure_table``
  fails — best-effort posture unchanged.
* The connection's ``rollback`` is called so the next stage sees a
  clean transaction.
* When the connector exposes neither ``_conn`` nor ``connection``,
  the runtime silently skips rollback (HTTP / Kafka / S3 sinks).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.inspect import ColumnInfo
from etl_plugins.core.pipeline import Pipeline, SinkSpec, Task
from etl_plugins.core.record import Record


class _StubSource(BatchSource):
    """Minimal source with two stable columns."""

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def health_check(self) -> bool:
        return True

    def list_tables(self) -> list[str]:
        return ["src_t"]

    def list_columns(self, table: str) -> list[ColumnInfo]:
        return [
            ColumnInfo(name="id", type="INTEGER"),
            ColumnInfo(name="name", type="TEXT"),
        ]

    def read(  # type: ignore[override]
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]:
        yield Record(data={"id": 1, "name": "a"})
        yield Record(data={"id": 2, "name": "b"})


class _FlakySink(BatchSink):
    """Sink whose ``ensure_table`` always raises (simulates a
    failed DDL) but whose ``write`` succeeds. Exposes a mock
    connection so we can assert ``rollback`` was called once."""

    def __init__(self) -> None:
        self._conn: Any = MagicMock(name="conn")
        self.write_called_with: list[Any] = []

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def health_check(self) -> bool:
        return True

    def ensure_table(
        self,
        table: str,
        columns: list[ColumnInfo],
        *,
        if_exists: str = "skip",
        primary_key: list[str] | None = None,
    ) -> None:
        raise RuntimeError(
            f"simulated CREATE TABLE failure for {table!r} "
            "(real-world cause: schema does not exist on destination)"
        )

    def write(
        self,
        records: Iterable[Record],
        *,
        table: str | None = None,
        **options: Any,
    ) -> int:
        rows = list(records)
        self.write_called_with.append((table, rows))
        return len(rows)


class _NoRollbackSink(_FlakySink):
    """Mirrors HTTP / Kafka / S3 sinks: no ``_conn`` / ``connection``
    attribute. The runtime must silently skip rollback."""

    def __init__(self) -> None:
        super().__init__()
        self._conn = None


def _make_task(source: BatchSource, sink: BatchSink) -> Task:
    # ``_auto_create_sink_tables`` reads the source's columns to pass
    # them to ``ensure_table``. Either ``source_options["table"]`` or
    # ``query`` (parsed to extract the first ``FROM`` ident) must be
    # present, otherwise the helper bails out before reaching the
    # sink. We give it a ``source_options["table"]`` so the path
    # exercises the connector contract end-to-end.
    return Task(
        name="t",
        source="src",
        query="SELECT * FROM src_t",
        source_options={"table": "src_t"},
        sink="dst",
        sink_table="dst_table",
        sink_auto_create_table=True,
        sink_auto_create_if_exists="skip",
    )


def test_auto_create_failure_triggers_rollback() -> None:
    """``rollback()`` must run after a suppressed ensure_table
    exception so the next operation doesn't trip over an aborted
    transaction."""
    src = _StubSource()
    sink = _FlakySink()
    task = _make_task(src, sink)

    pipeline = Pipeline(name="p", tasks=[task])
    spec = SinkSpec(
        name="dst",
        table="dst_table",
        mode="append",
        auto_create_table=True,
        auto_create_if_exists="skip",
    )
    pipeline._auto_create_sink_tables(task, src, [(spec, sink)])

    # The flaky sink's mock connection saw exactly one rollback call.
    sink._conn.rollback.assert_called_once()


def test_auto_create_failure_swallowed_pipeline_run_proceeds() -> None:
    """End-to-end: a sink whose DDL fails should NOT kill the run.
    Best-effort posture is preserved (Phase AAR / ADR-0066)."""
    src = _StubSource()
    sink = _FlakySink()
    task = _make_task(src, sink)

    pipeline = Pipeline(name="p", tasks=[task])
    result = pipeline.run(connectors={"src": src, "dst": sink})

    assert result.success
    assert result.records_written == 2
    assert sink.write_called_with, "sink.write must be invoked despite DDL failure"


def test_auto_create_failure_skips_rollback_when_no_connection_attr() -> None:
    """Sinks without ``_conn`` / ``connection`` (HTTP / Kafka / S3
    family) shouldn't crash — the rollback path is best-effort."""
    src = _StubSource()
    sink = _NoRollbackSink()
    task = _make_task(src, sink)
    pipeline = Pipeline(name="p", tasks=[task])
    spec = SinkSpec(
        name="dst",
        table="dst_table",
        mode="append",
        auto_create_table=True,
        auto_create_if_exists="skip",
    )
    # Must not raise.
    pipeline._auto_create_sink_tables(task, src, [(spec, sink)])


def test_postgres_ensure_table_uses_create_schema_if_not_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase AAR (2026-06-01) — Postgres ``ensure_table`` should
    emit ``CREATE SCHEMA IF NOT EXISTS`` before the
    ``CREATE TABLE`` when the destination is schema-qualified.
    Otherwise the DDL fails with ``schema "X" does not exist`` and
    the transaction goes aborted."""
    from etl_plugins.connectors.rdbms.postgres import PostgresConnector

    seen: list[str] = []

    class _Cursor:
        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
            seen.append(sql)

        def fetchone(self) -> tuple[Any, ...] | None:
            return None  # pretend the table doesn't yet exist

    class _Conn:
        def cursor(self) -> _Cursor:
            return _Cursor()

    c = PostgresConnector(host="x", database="x", user="u", password="p")
    c._conn = _Conn()  # type: ignore[assignment]
    c.ensure_table("BDA_BI_DB.TB_X", [ColumnInfo(name="id", type="BIGINT")])

    create_schema_stmts = [s for s in seen if "CREATE SCHEMA" in s]
    assert len(create_schema_stmts) == 1
    assert "BDA_BI_DB" in create_schema_stmts[0]
