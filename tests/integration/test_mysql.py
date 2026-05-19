"""MySQL connector integration tests [Step 5.1].

Same contract pattern as test_postgres.py — runs the standard BatchSource /
BatchSink / RoundTrip contracts against a real mysql container, plus
MySQL-specific tests for upsert / overwrite / error paths.
"""

from __future__ import annotations

from typing import Any

import pymysql
import pytest

from etl_plugins.connectors.rdbms.mysql import MySQLConnector
from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry
from tests.contracts.batch import (
    _BatchRoundTripContract,
    _BatchSinkContract,
    _BatchSourceContract,
)
from tests.contracts.cursor import _BatchSourceCursorContract

pytestmark = pytest.mark.it


# ---------- contract: BatchSource ----------


def _mysqlify(records: list[Record]) -> list[Record]:
    """MySQL stores BOOLEAN as TINYINT(1), so reads return 0/1 instead of True/False.

    The contract suite compares the read-back result to ``seeded_records`` payload-wise,
    so we project ``active`` to int up-front to match what MySQL will return.
    """
    out: list[Record] = []
    for r in records:
        data = dict(r.data)
        if "active" in data and isinstance(data["active"], bool):
            data["active"] = int(data["active"])
        out.append(Record(data=data, metadata=r.metadata, schema_version=r.schema_version))
    return out


class TestMySQLBatchSource(_BatchSourceContract):
    @pytest.fixture
    def source(self, mysql_connector: MySQLConnector, mysql_seeded: str) -> BatchSource:
        return mysql_connector

    @pytest.fixture
    def seeded_records(self, sample_records: list[Record]) -> list[Record]:
        return _mysqlify(sample_records)

    @pytest.fixture
    def read_kwargs(self, mysql_seeded: str) -> dict[str, object]:
        return {"query": f"SELECT id, name, age, active FROM `{mysql_seeded}` ORDER BY id"}


# ---------- contract: BatchSink ----------


class TestMySQLBatchSink(_BatchSinkContract):
    @pytest.fixture
    def sink(self, mysql_connector: MySQLConnector, mysql_table: str) -> BatchSink:
        return mysql_connector

    @pytest.fixture
    def write_kwargs(self, mysql_table: str) -> dict[str, object]:
        return {"table": mysql_table}


# ---------- contract: round-trip ----------


class TestMySQLRoundTrip(_BatchRoundTripContract):
    @pytest.fixture
    def round_trip_connector(
        self, mysql_connector: MySQLConnector, mysql_table: str
    ) -> BatchSource:
        return mysql_connector

    @pytest.fixture
    def sample_records(self, sample_records: list[Record]) -> list[Record]:
        """Override the global fixture: MySQL TINYINT(1) reads back as 0/1."""
        return _mysqlify(sample_records)

    @pytest.fixture
    def read_kwargs(self, mysql_table: str) -> dict[str, object]:
        return {"query": f"SELECT id, name, age, active FROM `{mysql_table}`"}

    @pytest.fixture
    def write_kwargs(self, mysql_table: str) -> dict[str, object]:
        return {"table": mysql_table}


# ---------- contract: cursored reads ----------


class TestMySQLCursorReads(_BatchSourceCursorContract):
    @pytest.fixture
    def cursor_source(self, mysql_connector: MySQLConnector, mysql_seeded: str) -> BatchSource:
        return mysql_connector

    @pytest.fixture
    def cursor_seeded_records(self, sample_records: list[Record]) -> list[Record]:
        return _mysqlify(sample_records)

    @pytest.fixture
    def cursor_column(self) -> str:
        return "id"

    @pytest.fixture
    def read_since_kwargs(self, mysql_seeded: str) -> dict[str, object]:
        return {"query": f"SELECT id, name, age, active FROM `{mysql_seeded}`"}


# ---------- mysql-specific tests ----------


def test_registry_resolves_mysql() -> None:
    """Entry-point 자동 발견이 작동해야 한다."""
    klass = ConnectorRegistry.get("mysql")
    assert klass is MySQLConnector
    assert klass.name == "mysql"


def test_health_check_false_before_connect(mysql_conn_params: dict[str, Any]) -> None:
    m = MySQLConnector(**mysql_conn_params)
    assert m.health_check() is False


def test_connect_bad_password_raises(mysql_conn_params: dict[str, Any]) -> None:
    bad = {**mysql_conn_params, "password": "definitely-wrong"}
    with pytest.raises(ConnectError):
        MySQLConnector(**bad).connect()


def test_read_without_query_raises(mysql_connector: MySQLConnector) -> None:
    mysql_connector.connect()
    with pytest.raises(ReadError, match="query"):
        list(mysql_connector.read())


def test_read_invalid_sql_raises_read_error(mysql_connector: MySQLConnector) -> None:
    mysql_connector.connect()
    with pytest.raises(ReadError):
        list(mysql_connector.read("SELECT * FROM completely_made_up_table_xyz"))


def test_write_without_table_raises(mysql_connector: MySQLConnector) -> None:
    mysql_connector.connect()
    with pytest.raises(WriteError, match="table"):
        mysql_connector.write(iter([Record(data={"id": 1})]))


def test_write_upsert_requires_key_columns(mysql_connector: MySQLConnector) -> None:
    mysql_connector.connect()
    with pytest.raises(WriteError, match="key_columns"):
        mysql_connector.write(iter([Record(data={"id": 1})]), table="x", mode="upsert")


def test_write_unknown_mode_raises(mysql_connector: MySQLConnector) -> None:
    mysql_connector.connect()
    with pytest.raises(WriteError, match="unknown write mode"):
        mysql_connector.write(iter([Record(data={"id": 1})]), table="x", mode="garbage")


def test_write_empty_input_returns_zero(mysql_connector: MySQLConnector, mysql_table: str) -> None:
    with mysql_connector:
        assert mysql_connector.write(iter([]), table=mysql_table) == 0


def test_overwrite_truncates_existing(
    mysql_connector: MySQLConnector,
    mysql_seeded: str,
    mysql_conn_params: dict[str, Any],
) -> None:
    new = [Record(data={"id": 99, "name": "X", "age": 0, "active": True})]
    with mysql_connector:
        n = mysql_connector.write(iter(new), table=mysql_seeded, mode="overwrite")
    assert n == 1
    with pymysql.connect(**mysql_conn_params) as raw, raw.cursor() as cur:
        cur.execute(f"SELECT id, name FROM `{mysql_seeded}`")
        rows = cur.fetchall()
    assert list(rows) == [(99, "X")]


def test_upsert_updates_existing_and_inserts_new(
    mysql_connector: MySQLConnector,
    mysql_seeded: str,
    mysql_conn_params: dict[str, Any],
) -> None:
    upsert_payload = [
        Record(data={"id": 1, "name": "Alice2", "age": 31, "active": False}),  # update
        Record(data={"id": 4, "name": "Dan", "age": 22, "active": True}),  # insert
    ]
    with mysql_connector:
        n = mysql_connector.write(
            iter(upsert_payload),
            table=mysql_seeded,
            mode="upsert",
            key_columns=["id"],
        )
    # MySQL returns affected-rows differently (insert=1, update=2 per row) but we
    # count by Python-side iteration, so n should equal the payload size.
    assert n == 2
    with pymysql.connect(**mysql_conn_params) as raw, raw.cursor() as cur:
        cur.execute(f"SELECT id, name, age FROM `{mysql_seeded}` ORDER BY id")
        rows = cur.fetchall()
    assert list(rows) == [
        (1, "Alice2", 31),
        (2, "Bob", 25),
        (3, "Carol", 35),
        (4, "Dan", 22),
    ]


def test_read_streams_through_server_side_cursor(
    mysql_connector: MySQLConnector, mysql_conn_params: dict[str, Any]
) -> None:
    table = "etl_chunk_test_mysql"
    with pymysql.connect(**mysql_conn_params) as raw:
        with raw.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS `{table}`")
            cur.execute(f"CREATE TABLE `{table}` (id INT)")
            cur.executemany(f"INSERT INTO `{table}` VALUES (%s)", [(i,) for i in range(500)])
        raw.commit()
    try:
        with mysql_connector:
            rows = list(mysql_connector.read(f"SELECT id FROM `{table}`", chunk_size=50))
        assert len(rows) == 500
        assert {r.data["id"] for r in rows} == set(range(500))
    finally:
        with pymysql.connect(**mysql_conn_params) as raw, raw.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS `{table}`")
            raw.commit()


def test_metadata_includes_source(mysql_connector: MySQLConnector, mysql_seeded: str) -> None:
    with mysql_connector:
        records = list(mysql_connector.read(f"SELECT * FROM `{mysql_seeded}` LIMIT 1"))
    assert records[0].metadata.get("source") == "mysql"


def test_identifier_quoting_handles_backtick(
    mysql_connector: MySQLConnector, mysql_conn_params: dict[str, Any]
) -> None:
    """A column named with a backtick should still write cleanly via our quoting."""
    table = "etl_quote_test"
    with pymysql.connect(**mysql_conn_params) as raw, raw.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS `{table}`")
        cur.execute(f"CREATE TABLE `{table}` (`a``b` INT, c INT)")
        raw.commit()
    try:
        with mysql_connector:
            n = mysql_connector.write(iter([Record(data={"a`b": 1, "c": 2})]), table=table)
        assert n == 1
        with pymysql.connect(**mysql_conn_params) as raw, raw.cursor() as cur:
            cur.execute(f"SELECT `a``b`, c FROM `{table}`")
            assert list(cur.fetchall()) == [(1, 2)]
    finally:
        with pymysql.connect(**mysql_conn_params) as raw, raw.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS `{table}`")
            raw.commit()
