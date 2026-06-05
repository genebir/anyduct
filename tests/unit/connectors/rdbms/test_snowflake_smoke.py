"""Driver-free tests for the Snowflake connector (Phase AGE, ADR-0077).

No live Snowflake account in unit tests, but with a fake cursor we can
prove the connector's *generated SQL* (CREATE TABLE with snowflake types,
INSERT, MERGE) plus the registry round-trip, protocol surface, and the
friendly "driver missing" error — everything except the network hop.
"""

from __future__ import annotations

from typing import Any

import pytest

from etl_plugins.connectors.rdbms.snowflake import SnowflakeConnector
from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, WriteError
from etl_plugins.core.inspect import ColumnInfo, SchemaInspector, SchemaWriter
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry

# ---------- registry + contract surface --------------------------------


def test_snowflake_resolves_through_registry() -> None:
    assert ConnectorRegistry.get("snowflake") is SnowflakeConnector


def test_snowflake_implements_all_capability_protocols() -> None:
    c = SnowflakeConnector(account="a", user="u", password="p")
    assert isinstance(c, SchemaInspector)
    assert isinstance(c, SchemaWriter)
    assert isinstance(c, BatchSource)
    assert isinstance(c, BatchSink)


def test_snowflake_connect_error_when_driver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "snowflake.connector", None)
    c = SnowflakeConnector(account="nowhere", user="u")
    with pytest.raises(ConnectError) as excinfo:
        c.connect()
    msg = str(excinfo.value)
    assert "snowflake-connector-python not installed" in msg
    assert "pip install" in msg


def test_snowflake_rejects_unsafe_table_identifier() -> None:
    c = SnowflakeConnector()
    with pytest.raises(WriteError, match="invalid table name"):
        c.ensure_table("orders; DROP", [ColumnInfo(name="id", type="NUMBER")])


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


def _bind(cur: _FakeCursor) -> SnowflakeConnector:
    c = SnowflakeConnector(account="a", user="u", password="p")
    c._conn = _FakeConn(cur)  # bypass connect(); inject the fake driver
    return c


def test_ensure_table_renders_snowflake_types() -> None:
    cur = _FakeCursor(table_exists=False)
    c = _bind(cur)
    c.ensure_table(
        "ORDERS",
        [
            ColumnInfo(name="ID", type="NUMBER(38,0)"),
            ColumnInfo(name="PAYLOAD", type="VARIANT"),
            ColumnInfo(name="NAME", type="VARCHAR(64)"),
            ColumnInfo(name="TS", type="TIMESTAMP_TZ"),
        ],
        primary_key=["ID"],
    )
    ddl = cur.executed[-1][0]
    assert ddl.startswith('CREATE TABLE "ORDERS" (')
    assert '"ID" NUMBER(38,0)' in ddl
    assert '"PAYLOAD" VARIANT' in ddl
    assert '"NAME" VARCHAR(64)' in ddl
    assert '"TS" TIMESTAMP_TZ' in ddl
    assert 'PRIMARY KEY ("ID")' in ddl


def test_ensure_table_skip_when_exists() -> None:
    cur = _FakeCursor(table_exists=True)
    c = _bind(cur)
    c.ensure_table("ORDERS", [ColumnInfo(name="ID", type="NUMBER")], if_exists="skip")
    # Only the existence check ran — no CREATE.
    assert all("CREATE TABLE" not in sql for sql, _ in cur.executed)


def test_write_append_builds_parameterised_insert() -> None:
    cur = _FakeCursor()
    c = _bind(cur)
    n = c.write(
        [Record(data={"ID": 1, "NAME": "a"}), Record(data={"ID": 2, "NAME": "b"})],
        table="ORDERS",
        mode="append",
    )
    assert n == 2
    sql, rows = cur.executemany_calls[0]
    assert sql == 'INSERT INTO "ORDERS" ("ID", "NAME") VALUES (%s, %s)'
    assert rows == [(1, "a"), (2, "b")]


def test_write_upsert_builds_merge() -> None:
    cur = _FakeCursor()
    c = _bind(cur)
    n = c.write(
        [Record(data={"ID": 1, "NAME": "a"})],
        table="ORDERS",
        mode="upsert",
        key_columns=["ID"],
    )
    assert n == 1
    merge_sql = cur.executed[-1][0]
    assert merge_sql.startswith('MERGE INTO "ORDERS" tgt')
    assert "WHEN MATCHED THEN UPDATE SET" in merge_sql
    assert "WHEN NOT MATCHED THEN INSERT" in merge_sql
    assert 'tgt."ID" = src."ID"' in merge_sql


def test_write_requires_table() -> None:
    c = _bind(_FakeCursor())
    with pytest.raises(WriteError, match="requires 'table'"):
        c.write([Record(data={"ID": 1})], table=None)
