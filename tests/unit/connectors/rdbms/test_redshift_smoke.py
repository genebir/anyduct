"""Driver-free tests for the Redshift connector (Phase AGG, ADR-0079).

No live Redshift cluster in unit tests, but a fake cursor proves the
connector's generated SQL (double-quoted CREATE TABLE with Redshift
types + PK, INSERT, MERGE) plus the registry round-trip, protocol
surface, and friendly "driver missing" error.
"""

from __future__ import annotations

from typing import Any

import pytest

from etl_plugins.connectors.rdbms.redshift import RedshiftConnector
from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, WriteError
from etl_plugins.core.inspect import ColumnInfo, SchemaInspector, SchemaWriter
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


def test_redshift_resolves_through_registry() -> None:
    assert ConnectorRegistry.get("redshift") is RedshiftConnector


def test_redshift_implements_all_capability_protocols() -> None:
    c = RedshiftConnector(host="h", database="d", user="u", password="p")
    assert isinstance(c, SchemaInspector)
    assert isinstance(c, SchemaWriter)
    assert isinstance(c, BatchSource)
    assert isinstance(c, BatchSink)


def test_redshift_connect_error_when_driver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "redshift_connector", None)
    c = RedshiftConnector(host="nowhere", database="d")
    with pytest.raises(ConnectError) as excinfo:
        c.connect()
    msg = str(excinfo.value)
    assert "redshift_connector not installed" in msg
    assert "pip install" in msg


def test_redshift_rejects_unsafe_table_identifier() -> None:
    c = RedshiftConnector()
    with pytest.raises(WriteError, match="invalid table name"):
        c.ensure_table("orders; DROP", [ColumnInfo(name="id", type="INTEGER")])


# ---------- fake-cursor SQL generation ---------------------------------


class _FakeCursor:
    def __init__(self, *, table_exists: bool = False) -> None:
        self.executed: list[tuple[str, Any]] = []
        self.executemany_calls: list[tuple[str, list]] = []
        self._table_exists = table_exists
        self.rowcount = 0
        self.description = None

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def executemany(self, sql: str, seq: Any) -> None:
        self.executemany_calls.append((sql, list(seq)))

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

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def _bind(cur: _FakeCursor) -> RedshiftConnector:
    c = RedshiftConnector(host="h", database="d", user="u", password="p")
    c._conn = _FakeConn(cur)
    return c


def test_ensure_table_renders_redshift_types() -> None:
    cur = _FakeCursor(table_exists=False)
    c = _bind(cur)
    c.ensure_table(
        "orders",
        [
            ColumnInfo(name="id", type="BIGINT"),
            ColumnInfo(name="payload", type="SUPER"),
            ColumnInfo(name="note", type="TEXT"),
            ColumnInfo(name="ts", type="TIMESTAMPTZ"),
        ],
        primary_key=["id"],
    )
    ddl = cur.executed[-1][0]
    assert ddl.startswith('CREATE TABLE "orders" (')
    assert '"id" BIGINT' in ddl
    assert '"payload" SUPER' in ddl
    assert '"note" VARCHAR(65535)' in ddl  # Redshift has no TEXT type
    assert '"ts" TIMESTAMPTZ' in ddl
    assert 'PRIMARY KEY ("id")' in ddl


def test_ensure_table_skip_when_exists() -> None:
    cur = _FakeCursor(table_exists=True)
    c = _bind(cur)
    c.ensure_table("orders", [ColumnInfo(name="id", type="BIGINT")], if_exists="skip")
    assert all("CREATE TABLE" not in sql for sql, _ in cur.executed)


def test_write_append_builds_parameterised_insert() -> None:
    cur = _FakeCursor()
    c = _bind(cur)
    n = c.write(
        [Record(data={"id": 1, "name": "a"}), Record(data={"id": 2, "name": "b"})],
        table="orders",
        mode="append",
    )
    assert n == 2
    sql, rows = cur.executemany_calls[0]
    assert sql == 'INSERT INTO "orders" ("id", "name") VALUES (%s, %s)'
    assert rows == [(1, "a"), (2, "b")]


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
    assert merge_sql.startswith('MERGE INTO "orders" tgt')
    assert "WHEN MATCHED THEN UPDATE SET" in merge_sql
    assert 'tgt."id" = src."id"' in merge_sql


def test_overwrite_deletes_then_inserts() -> None:
    cur = _FakeCursor()
    c = _bind(cur)
    c.write([Record(data={"id": 1})], table="orders", mode="overwrite")
    assert any(sql == 'DELETE FROM "orders"' for sql, _ in cur.executed)


def test_write_requires_table() -> None:
    c = _bind(_FakeCursor())
    with pytest.raises(WriteError, match="requires 'table'"):
        c.write([Record(data={"id": 1})], table=None)
