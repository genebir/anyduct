"""Driver-free tests for the ClickHouse connector (Phase AGH, ADR-0080).

No live ClickHouse in unit tests, but a fake cursor proves the generated
SQL (backtick CREATE TABLE with ClickHouse types + MergeTree/ORDER BY,
multi-row INSERT, TRUNCATE overwrite, upsert-unsupported error, Nullable
unwrapping) plus the registry round-trip, protocol surface, and friendly
"driver missing" error.
"""

from __future__ import annotations

from typing import Any

import pytest

from etl_plugins.connectors.rdbms.clickhouse import ClickHouseConnector, _unwrap_ch_type
from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, WriteError
from etl_plugins.core.inspect import ColumnInfo, SchemaInspector, SchemaWriter
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


def test_clickhouse_resolves_through_registry() -> None:
    assert ConnectorRegistry.get("clickhouse") is ClickHouseConnector


def test_clickhouse_implements_all_capability_protocols() -> None:
    c = ClickHouseConnector(host="h", database="d")
    assert isinstance(c, SchemaInspector)
    assert isinstance(c, SchemaWriter)
    assert isinstance(c, BatchSource)
    assert isinstance(c, BatchSink)


def test_clickhouse_connect_error_when_driver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "clickhouse_connect", None)
    monkeypatch.setitem(sys.modules, "clickhouse_connect.dbapi", None)
    c = ClickHouseConnector(host="nowhere")
    with pytest.raises(ConnectError) as excinfo:
        c.connect()
    msg = str(excinfo.value)
    assert "clickhouse-connect not installed" in msg
    assert "pip install" in msg


def test_clickhouse_rejects_unsafe_table_identifier() -> None:
    c = ClickHouseConnector()
    with pytest.raises(WriteError, match="invalid table name"):
        c.ensure_table("orders; DROP", [ColumnInfo(name="id", type="Int64")])


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Nullable(Int64)", "Int64"),
        ("LowCardinality(String)", "String"),
        ("LowCardinality(Nullable(String))", "String"),
        ("Int32", "Int32"),
        ("DateTime64(3)", "DateTime64(3)"),
    ],
)
def test_unwrap_ch_type(raw: str, expected: str) -> None:
    assert _unwrap_ch_type(raw) == expected


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


def _bind(cur: _FakeCursor) -> ClickHouseConnector:
    c = ClickHouseConnector(host="h", database="db")
    c._conn = _FakeConn(cur)
    return c


def test_ensure_table_renders_clickhouse_types_mergetree() -> None:
    cur = _FakeCursor(table_exists=False)
    c = _bind(cur)
    c.ensure_table(
        "orders",
        [
            ColumnInfo(name="id", type="Int64"),
            ColumnInfo(name="amount", type="Decimal(10,2)"),
            ColumnInfo(name="name", type="String"),
            ColumnInfo(name="ts", type="TIMESTAMPTZ"),
        ],
        primary_key=["id"],
    )
    ddl = cur.executed[-1][0]
    assert ddl.startswith("CREATE TABLE `db`.`orders` (")
    assert "`id` Int64" in ddl
    assert "`amount` Decimal(10,2)" in ddl
    assert "`name` String" in ddl
    assert "`ts` DateTime64(3)" in ddl
    assert "ENGINE = MergeTree ORDER BY (`id`)" in ddl


def test_ensure_table_no_pk_uses_tuple_order() -> None:
    cur = _FakeCursor(table_exists=False)
    c = _bind(cur)
    c.ensure_table("orders", [ColumnInfo(name="id", type="Int64")])
    assert "ENGINE = MergeTree ORDER BY tuple()" in cur.executed[-1][0]


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
    assert sql == "INSERT INTO `db`.`orders` (`id`, `name`) VALUES (%s, %s), (%s, %s)"
    assert params == [1, "a", 2, "b"]


def test_overwrite_truncates() -> None:
    cur = _FakeCursor()
    c = _bind(cur)
    c.write([Record(data={"id": 1})], table="orders", mode="overwrite")
    assert any(sql == "TRUNCATE TABLE `db`.`orders`" for sql, _ in cur.executed)


def test_upsert_unsupported() -> None:
    c = _bind(_FakeCursor())
    with pytest.raises(WriteError, match="no row-level UPSERT"):
        c.write([Record(data={"id": 1})], table="orders", mode="upsert", key_columns=["id"])


def test_write_requires_table() -> None:
    c = _bind(_FakeCursor())
    with pytest.raises(WriteError, match="requires 'table'"):
        c.write([Record(data={"id": 1})], table=None)
