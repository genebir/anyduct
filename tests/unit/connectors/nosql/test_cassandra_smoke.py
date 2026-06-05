"""Driver-free tests for the Cassandra connector (Phase AGK, ADR-0082).

No live Cassandra in unit tests (cassandra-driver is a heavy C-extension
+ slow container), so — like the DW connectors — a fake session proves
the generated CQL (CREATE TABLE with PRIMARY KEY, INSERT, TRUNCATE) plus
the registry round-trip, protocol surface, contact-point parsing, and the
friendly "driver missing" error.
"""

from __future__ import annotations

from typing import Any

import pytest

from etl_plugins.connectors.nosql.cassandra import CassandraConnector
from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, WriteError
from etl_plugins.core.inspect import ColumnInfo, SchemaInspector, SchemaWriter
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


def test_cassandra_resolves_through_registry() -> None:
    assert ConnectorRegistry.get("cassandra") is CassandraConnector


def test_cassandra_implements_all_capability_protocols() -> None:
    # CQL is tabular, so unlike DynamoDB it IS a SchemaInspector/Writer.
    c = CassandraConnector(contact_points="h", keyspace="k")
    assert isinstance(c, SchemaInspector)
    assert isinstance(c, SchemaWriter)
    assert isinstance(c, BatchSource)
    assert isinstance(c, BatchSink)


def test_contact_points_string_parsed_to_list() -> None:
    c = CassandraConnector(contact_points="a, b ,c")
    assert c.contact_points == ["a", "b", "c"]


def test_cassandra_connect_error_when_driver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "cassandra.auth", None)
    monkeypatch.setitem(sys.modules, "cassandra.cluster", None)
    c = CassandraConnector(contact_points="nowhere")
    with pytest.raises(ConnectError) as excinfo:
        c.connect()
    msg = str(excinfo.value)
    assert "cassandra-driver not installed" in msg
    assert "pip install" in msg


def test_cassandra_rejects_unsafe_table_identifier() -> None:
    c = _bind(_FakeSession())
    with pytest.raises(WriteError, match="invalid table name"):
        c.ensure_table("orders; DROP", [ColumnInfo(name="id", type="int")])


# ---------- fake-session CQL generation --------------------------------


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def one(self) -> Any:
        return self._rows[0] if self._rows else None

    def __iter__(self) -> Any:
        return iter(self._rows)


class _FakeSession:
    def __init__(self, *, table_exists: bool = False) -> None:
        self.executed: list[tuple[str, Any]] = []
        self._exists = table_exists

    def execute(self, cql: str, params: Any = None) -> _FakeResult:
        self.executed.append((cql, params))
        if "system_schema.tables" in cql and params is not None:
            return _FakeResult([("t",)] if self._exists else [])
        return _FakeResult([])


def _bind(session: _FakeSession) -> CassandraConnector:
    c = CassandraConnector(contact_points="h", keyspace="ks")
    c._session = session
    return c


def test_ensure_table_renders_cql_with_primary_key() -> None:
    s = _FakeSession(table_exists=False)
    c = _bind(s)
    c.ensure_table(
        "ks.orders",
        [
            ColumnInfo(name="id", type="bigint"),
            ColumnInfo(name="amount", type="decimal(10,2)"),
            ColumnInfo(name="name", type="text"),
        ],
        primary_key=["id"],
    )
    ddl = s.executed[-1][0]
    assert ddl.startswith('CREATE TABLE "ks"."orders" (')
    assert '"id" bigint' in ddl
    # Cassandra decimal takes no (p,s).
    assert '"amount" decimal' in ddl
    assert "decimal(10,2)" not in ddl
    assert '"name" text' in ddl
    assert 'PRIMARY KEY ("id")' in ddl


def test_ensure_table_defaults_pk_to_first_column() -> None:
    s = _FakeSession(table_exists=False)
    c = _bind(s)
    c.ensure_table("ks.t", [ColumnInfo(name="k", type="text"), ColumnInfo(name="v", type="int")])
    assert 'PRIMARY KEY ("k")' in s.executed[-1][0]


def test_ensure_table_skip_when_exists() -> None:
    s = _FakeSession(table_exists=True)
    c = _bind(s)
    c.ensure_table("ks.orders", [ColumnInfo(name="id", type="int")], if_exists="skip")
    assert all("CREATE TABLE" not in cql for cql, _ in s.executed)


def test_write_append_inserts_each_row() -> None:
    s = _FakeSession()
    c = _bind(s)
    n = c.write(
        [Record(data={"id": 1, "name": "a"}), Record(data={"id": 2, "name": "b"})],
        table="ks.orders",
        mode="append",
    )
    assert n == 2
    inserts = [(cql, p) for cql, p in s.executed if cql.startswith("INSERT")]
    assert inserts[0][0] == 'INSERT INTO "ks"."orders" ("id", "name") VALUES (%s, %s)'
    assert [p for _, p in inserts] == [(1, "a"), (2, "b")]


def test_overwrite_truncates_first() -> None:
    s = _FakeSession()
    c = _bind(s)
    c.write([Record(data={"id": 1})], table="ks.orders", mode="overwrite")
    assert any(cql == 'TRUNCATE "ks"."orders"' for cql, _ in s.executed)


def test_write_requires_table() -> None:
    c = _bind(_FakeSession())
    with pytest.raises(WriteError, match="requires 'table'"):
        c.write([Record(data={"id": 1})], table=None)
