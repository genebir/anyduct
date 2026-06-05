"""Driver-free tests for the BigQuery connector (Phase AGF, ADR-0078).

No live BigQuery project in unit tests, but a fake cursor proves the
connector's generated GoogleSQL (backtick-quoted CREATE TABLE with
BigQuery types + ``NOT ENFORCED`` PK, multi-row INSERT, MERGE) plus the
registry round-trip, protocol surface, and friendly "driver missing"
error.
"""

from __future__ import annotations

from typing import Any

import pytest

from etl_plugins.connectors.rdbms.bigquery import BigQueryConnector
from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, WriteError
from etl_plugins.core.inspect import ColumnInfo, SchemaInspector, SchemaWriter
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry

# ---------- registry + contract surface --------------------------------


def test_bigquery_resolves_through_registry() -> None:
    assert ConnectorRegistry.get("bigquery") is BigQueryConnector


def test_bigquery_implements_all_capability_protocols() -> None:
    c = BigQueryConnector(project="p", dataset="d")
    assert isinstance(c, SchemaInspector)
    assert isinstance(c, SchemaWriter)
    assert isinstance(c, BatchSource)
    assert isinstance(c, BatchSink)


def test_bigquery_connect_error_when_driver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", None)
    c = BigQueryConnector(project="nowhere", dataset="d")
    with pytest.raises(ConnectError) as excinfo:
        c.connect()
    msg = str(excinfo.value)
    assert "google-cloud-bigquery not installed" in msg
    assert "pip install" in msg


def test_bigquery_rejects_unsafe_table_identifier() -> None:
    c = BigQueryConnector(dataset="d")
    with pytest.raises(WriteError, match="invalid table name"):
        c.ensure_table("orders; DROP", [ColumnInfo(name="id", type="INT64")])


# ---------- fake-cursor SQL generation ---------------------------------


class _FakeCursor:
    def __init__(self, *, table_exists: bool = False) -> None:
        self.executed: list[tuple[str, Any]] = []
        self._table_exists = table_exists
        self.rowcount = 0
        self.description = None

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def fetchone(self) -> Any:
        return (1,) if self._table_exists else None

    def fetchall(self) -> list:
        return []

    def close(self) -> None:
        pass


class _FakeConn:
    def __init__(self, cur: _FakeCursor) -> None:
        self._cur = cur

    def cursor(self) -> _FakeCursor:
        return self._cur

    def close(self) -> None:
        pass


def _bind(cur: _FakeCursor, *, dataset: str = "ds") -> BigQueryConnector:
    c = BigQueryConnector(project="proj", dataset=dataset)
    c._conn = _FakeConn(cur)
    return c


def test_ensure_table_renders_bigquery_types_with_backticks() -> None:
    cur = _FakeCursor(table_exists=False)
    c = _bind(cur)
    c.ensure_table(
        "orders",
        [
            ColumnInfo(name="id", type="INT64"),
            ColumnInfo(name="payload", type="JSON"),
            ColumnInfo(name="name", type="STRING"),
            ColumnInfo(name="amount", type="NUMERIC(10,2)"),
        ],
        primary_key=["id"],
    )
    ddl = cur.executed[-1][0]
    assert ddl.startswith("CREATE TABLE `ds.orders` (")
    assert "`id` INT64" in ddl
    assert "`payload` JSON" in ddl
    assert "`name` STRING" in ddl
    assert "`amount` NUMERIC(10,2)" in ddl
    assert "PRIMARY KEY (`id`) NOT ENFORCED" in ddl


def test_ensure_table_skip_when_exists() -> None:
    cur = _FakeCursor(table_exists=True)
    c = _bind(cur)
    c.ensure_table("orders", [ColumnInfo(name="id", type="INT64")], if_exists="skip")
    assert all("CREATE TABLE" not in sql for sql, _ in cur.executed)


def test_qualified_table_path_kept() -> None:
    cur = _FakeCursor(table_exists=False)
    c = _bind(cur)
    c.ensure_table("myproj.myds.orders", [ColumnInfo(name="id", type="INT64")])
    assert "CREATE TABLE `myproj.myds.orders` (" in cur.executed[-1][0]


def test_write_append_builds_multirow_insert() -> None:
    cur = _FakeCursor()
    c = _bind(cur)
    n = c.write(
        [Record(data={"id": 1, "name": "a"}), Record(data={"id": 2, "name": "b"})],
        table="orders",
        mode="append",
    )
    assert n == 2
    sql, params = cur.executed[-1]
    assert sql == "INSERT INTO `ds.orders` (`id`, `name`) VALUES (%s, %s), (%s, %s)"
    assert params == [1, "a", 2, "b"]


def test_write_upsert_builds_merge() -> None:
    cur = _FakeCursor()
    c = _bind(cur)
    n = c.write(
        [Record(data={"id": 1, "name": "a"})],
        table="orders",
        mode="upsert",
        key_columns=["id"],
    )
    assert n == 1
    merge_sql = cur.executed[-1][0]
    assert merge_sql.startswith("MERGE INTO `ds.orders` tgt")
    assert "WHEN MATCHED THEN UPDATE SET" in merge_sql
    assert "tgt.`id` = src.`id`" in merge_sql


def test_overwrite_clears_with_delete_where_true() -> None:
    cur = _FakeCursor()
    c = _bind(cur)
    c.write([Record(data={"id": 1})], table="orders", mode="overwrite")
    delete_sqls = [sql for sql, _ in cur.executed if sql.startswith("DELETE FROM")]
    assert delete_sqls == ["DELETE FROM `ds.orders` WHERE TRUE"]


def test_list_tables_requires_dataset() -> None:
    from etl_plugins.core.exceptions import ReadError

    c = _bind(_FakeCursor(), dataset="")
    with pytest.raises(ReadError, match="requires a 'dataset'"):
        c.list_tables()
