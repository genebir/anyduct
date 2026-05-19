"""PostgreSQL connector integration tests.

Runs the standard BatchSource / BatchSink / RoundTrip contracts against a real
postgres container, plus a few postgres-specific tests for upsert / overwrite
/ error paths.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from etl_plugins.connectors.rdbms.postgres import PostgresConnector
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


class TestPostgresBatchSource(_BatchSourceContract):
    @pytest.fixture
    def source(self, pg_connector: PostgresConnector, pg_seeded: str) -> BatchSource:
        # pg_seeded ensures the table is populated; pg_connector is unconnected.
        return pg_connector

    @pytest.fixture
    def seeded_records(self, sample_records: list[Record]) -> list[Record]:
        return sample_records

    @pytest.fixture
    def read_kwargs(self, pg_seeded: str) -> dict[str, object]:
        return {"query": f"SELECT id, name, age, active FROM {pg_seeded} ORDER BY id"}


# ---------- contract: BatchSink ----------


class TestPostgresBatchSink(_BatchSinkContract):
    @pytest.fixture
    def sink(self, pg_connector: PostgresConnector, pg_table: str) -> BatchSink:
        return pg_connector

    @pytest.fixture
    def write_kwargs(self, pg_table: str) -> dict[str, object]:
        return {"table": pg_table}


# ---------- contract: round-trip ----------


class TestPostgresRoundTrip(_BatchRoundTripContract):
    @pytest.fixture
    def round_trip_connector(self, pg_connector: PostgresConnector, pg_table: str) -> BatchSource:
        return pg_connector

    @pytest.fixture
    def read_kwargs(self, pg_table: str) -> dict[str, object]:
        return {"query": f"SELECT id, name, age, active FROM {pg_table}"}

    @pytest.fixture
    def write_kwargs(self, pg_table: str) -> dict[str, object]:
        return {"table": pg_table}


# ---------- contract: cursored reads ----------


class TestPostgresCursorReads(_BatchSourceCursorContract):
    @pytest.fixture
    def cursor_source(self, pg_connector: PostgresConnector, pg_seeded: str) -> BatchSource:
        return pg_connector

    @pytest.fixture
    def cursor_seeded_records(self, sample_records: list[Record]) -> list[Record]:
        return sample_records

    @pytest.fixture
    def cursor_column(self) -> str:
        return "id"

    @pytest.fixture
    def read_since_kwargs(self, pg_seeded: str) -> dict[str, object]:
        return {"query": f"SELECT id, name, age, active FROM {pg_seeded}"}

    @pytest.fixture
    def read_kwargs(self, pg_table: str) -> dict[str, object]:
        return {"query": f"SELECT id, name, age, active FROM {pg_table}"}

    @pytest.fixture
    def write_kwargs(self, pg_table: str) -> dict[str, object]:
        return {"table": pg_table}


# ---------- postgres-specific tests ----------


def test_registry_resolves_postgres() -> None:
    """Entry-point 자동 발견이 작동해야 한다."""
    klass = ConnectorRegistry.get("postgres")
    assert klass is PostgresConnector
    assert klass.name == "postgres"


def test_health_check_false_before_connect(pg_conn_params: dict[str, Any]) -> None:
    pg = PostgresConnector(**pg_conn_params)
    assert pg.health_check() is False


def test_connect_bad_password_raises(pg_conn_params: dict[str, Any]) -> None:
    bad = {**pg_conn_params, "password": "definitely-wrong"}
    with pytest.raises(ConnectError):
        PostgresConnector(**bad).connect()


def test_read_without_query_raises(pg_connector: PostgresConnector) -> None:
    pg_connector.connect()
    with pytest.raises(ReadError, match="query"):
        list(pg_connector.read())


def test_read_invalid_sql_raises_read_error(pg_connector: PostgresConnector) -> None:
    pg_connector.connect()
    with pytest.raises(ReadError):
        list(pg_connector.read("SELECT * FROM completely_made_up_table_xyz"))


def test_write_without_table_raises(pg_connector: PostgresConnector) -> None:
    pg_connector.connect()
    with pytest.raises(WriteError, match="table"):
        pg_connector.write(iter([Record(data={"id": 1})]))


def test_write_upsert_requires_key_columns(pg_connector: PostgresConnector) -> None:
    pg_connector.connect()
    with pytest.raises(WriteError, match="key_columns"):
        pg_connector.write(iter([Record(data={"id": 1})]), table="x", mode="upsert")


def test_write_unknown_mode_raises(pg_connector: PostgresConnector) -> None:
    pg_connector.connect()
    with pytest.raises(WriteError, match="unknown write mode"):
        pg_connector.write(iter([Record(data={"id": 1})]), table="x", mode="garbage")


def test_write_empty_input_returns_zero(pg_connector: PostgresConnector, pg_table: str) -> None:
    with pg_connector:
        assert pg_connector.write(iter([]), table=pg_table) == 0


def test_overwrite_truncates_existing(
    pg_connector: PostgresConnector,
    pg_seeded: str,
    pg_raw_kwargs: dict[str, Any],
) -> None:
    new = [Record(data={"id": 99, "name": "X", "age": 0, "active": True})]
    with pg_connector:
        n = pg_connector.write(iter(new), table=pg_seeded, mode="overwrite")
    assert n == 1
    # Verify only the new row remains
    with psycopg.connect(**pg_raw_kwargs) as raw, raw.cursor() as cur:
        cur.execute(f"SELECT id, name FROM {pg_seeded}")
        rows = cur.fetchall()
    assert rows == [(99, "X")]


def test_upsert_updates_existing_and_inserts_new(
    pg_connector: PostgresConnector,
    pg_seeded: str,
    pg_raw_kwargs: dict[str, Any],
) -> None:
    upsert_payload = [
        Record(data={"id": 1, "name": "Alice2", "age": 31, "active": False}),  # update
        Record(data={"id": 4, "name": "Dan", "age": 22, "active": True}),  # insert
    ]
    with pg_connector:
        n = pg_connector.write(
            iter(upsert_payload),
            table=pg_seeded,
            mode="upsert",
            key_columns=["id"],
        )
    assert n == 2
    with psycopg.connect(**pg_raw_kwargs) as raw, raw.cursor() as cur:
        cur.execute(f"SELECT id, name, age FROM {pg_seeded} ORDER BY id")
        rows = cur.fetchall()
    # id=1 updated, id=2,3 unchanged, id=4 inserted
    assert rows == [
        (1, "Alice2", 31),
        (2, "Bob", 25),
        (3, "Carol", 35),
        (4, "Dan", 22),
    ]


def test_read_streams_through_server_side_cursor(
    pg_connector: PostgresConnector, pg_raw_kwargs: dict[str, Any]
) -> None:
    """큰 결과 셋도 chunk_size 단위로 스트리밍되어야 한다."""
    table = f"etl_chunk_test_{0:08x}"  # arbitrary fixed name
    with psycopg.connect(**pg_raw_kwargs) as raw:
        with raw.cursor() as cur:
            cur.execute(f"CREATE TABLE {table} (id INT)")
            cur.executemany(f"INSERT INTO {table} VALUES (%s)", [(i,) for i in range(500)])
        raw.commit()
    try:
        with pg_connector:
            rows = list(pg_connector.read(f"SELECT id FROM {table}", chunk_size=50))
        assert len(rows) == 500
        assert {r.data["id"] for r in rows} == set(range(500))
    finally:
        with psycopg.connect(**pg_raw_kwargs) as raw, raw.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
            raw.commit()


def test_metadata_includes_source(pg_connector: PostgresConnector, pg_seeded: str) -> None:
    with pg_connector:
        records = list(pg_connector.read(f"SELECT * FROM {pg_seeded} LIMIT 1"))
    assert records[0].metadata.get("source") == "postgres"


def test_schema_qualified_table_name(
    pg_connector: PostgresConnector, pg_raw_kwargs: dict[str, Any]
) -> None:
    """``public.<table>`` 형식도 정상 처리되어야 한다."""
    table = f"etl_schema_test_{0:08x}"
    with psycopg.connect(**pg_raw_kwargs) as raw:
        with raw.cursor() as cur:
            cur.execute(f"CREATE TABLE public.{table} (id INT, name TEXT)")
        raw.commit()
    try:
        records = [Record(data={"id": 1, "name": "a"})]
        with pg_connector:
            n = pg_connector.write(iter(records), table=f"public.{table}")
        assert n == 1
    finally:
        with psycopg.connect(**pg_raw_kwargs) as raw, raw.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS public.{table}")
            raw.commit()
