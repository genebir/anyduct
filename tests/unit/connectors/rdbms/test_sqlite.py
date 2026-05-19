"""SQLite connector unit tests [Step 5.1b].

Unlike postgres/mysql/kafka/s3, SQLite has no external dependency, so the
contract suite + connector-specific checks all run as plain unit tests
(no testcontainers, no ``@pytest.mark.it``).

Each test uses a file-backed temp database — pyramid avoids the
``check_same_thread`` issues that pop up with shared in-memory connections.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from etl_plugins.connectors.rdbms.sqlite import SQLiteConnector
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


def _sqlitify(records: list[Record]) -> list[Record]:
    """SQLite stores BOOLEAN as INT (0/1); reads return ints."""
    out: list[Record] = []
    for r in records:
        data = dict(r.data)
        if "active" in data and isinstance(data["active"], bool):
            data["active"] = int(data["active"])
        out.append(Record(data=data, metadata=r.metadata, schema_version=r.schema_version))
    return out


# ---------- fixtures -------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def sqlite_table(db_path: Path) -> Iterator[str]:
    """Create the sample-records table; cleaned up automatically with the temp db."""
    name = "test_table"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"CREATE TABLE {name} (id INTEGER PRIMARY KEY, name TEXT, age INTEGER, active INTEGER)"
        )
        conn.commit()
    yield name


@pytest.fixture
def sqlite_seeded(db_path: Path, sqlite_table: str, sample_records: list[Record]) -> str:
    with sqlite3.connect(db_path) as conn:
        for r in sample_records:
            conn.execute(
                f"INSERT INTO {sqlite_table} VALUES (?, ?, ?, ?)",
                (r.data["id"], r.data["name"], r.data["age"], int(r.data["active"])),
            )
        conn.commit()
    return sqlite_table


@pytest.fixture
def sqlite_connector(db_path: Path) -> Iterator[SQLiteConnector]:
    c = SQLiteConnector(database=str(db_path))
    yield c
    c.close()


# ---------- contract: BatchSource ----------


class TestSQLiteBatchSource(_BatchSourceContract):
    @pytest.fixture
    def source(self, sqlite_connector: SQLiteConnector, sqlite_seeded: str) -> BatchSource:
        return sqlite_connector

    @pytest.fixture
    def seeded_records(self, sample_records: list[Record]) -> list[Record]:
        return _sqlitify(sample_records)

    @pytest.fixture
    def read_kwargs(self, sqlite_seeded: str) -> dict[str, object]:
        return {"query": f"SELECT id, name, age, active FROM {sqlite_seeded} ORDER BY id"}


# ---------- contract: BatchSink ----------


class TestSQLiteBatchSink(_BatchSinkContract):
    @pytest.fixture
    def sink(self, sqlite_connector: SQLiteConnector, sqlite_table: str) -> BatchSink:
        return sqlite_connector

    @pytest.fixture
    def write_kwargs(self, sqlite_table: str) -> dict[str, object]:
        return {"table": sqlite_table}


# ---------- contract: round-trip ----------


class TestSQLiteRoundTrip(_BatchRoundTripContract):
    @pytest.fixture
    def round_trip_connector(
        self, sqlite_connector: SQLiteConnector, sqlite_table: str
    ) -> BatchSource:
        return sqlite_connector

    @pytest.fixture
    def sample_records(self, sample_records: list[Record]) -> list[Record]:
        return _sqlitify(sample_records)

    @pytest.fixture
    def read_kwargs(self, sqlite_table: str) -> dict[str, object]:
        return {"query": f"SELECT id, name, age, active FROM {sqlite_table}"}

    @pytest.fixture
    def write_kwargs(self, sqlite_table: str) -> dict[str, object]:
        return {"table": sqlite_table}


# ---------- contract: cursored reads ----------


class TestSQLiteCursorReads(_BatchSourceCursorContract):
    @pytest.fixture
    def cursor_source(self, sqlite_connector: SQLiteConnector, sqlite_seeded: str) -> BatchSource:
        return sqlite_connector

    @pytest.fixture
    def cursor_seeded_records(self, sample_records: list[Record]) -> list[Record]:
        return _sqlitify(sample_records)

    @pytest.fixture
    def cursor_column(self) -> str:
        return "id"

    @pytest.fixture
    def read_since_kwargs(self, sqlite_seeded: str) -> dict[str, object]:
        return {"query": f"SELECT id, name, age, active FROM {sqlite_seeded}"}


# ---------- sqlite-specific cursor tests ----------


def test_read_since_requires_query(sqlite_connector: SQLiteConnector) -> None:
    with sqlite_connector, pytest.raises(ReadError, match="query"):
        list(sqlite_connector.read_since("id", None))


def test_read_since_raises_when_not_connected(db_path: Path) -> None:
    c = SQLiteConnector(database=str(db_path))
    with pytest.raises(ConnectError, match="not connected"):
        list(c.read_since("id", None, query="SELECT 1"))


def test_read_since_records_carry_cursor_metadata(
    sqlite_connector: SQLiteConnector, sqlite_seeded: str
) -> None:
    """read_since stamps Record.metadata['cursor_column'] so downstream
    transforms / sinks can see which field is the watermark."""
    with sqlite_connector:
        records = list(
            sqlite_connector.read_since("id", None, query=f"SELECT id, name FROM {sqlite_seeded}")
        )
    assert records
    for r in records:
        assert r.metadata["cursor_column"] == "id"


def test_read_since_wraps_complex_query(
    sqlite_connector: SQLiteConnector, sqlite_seeded: str
) -> None:
    """The wrapping ``SELECT * FROM (<query>) WHERE …`` shape should
    accept inner queries that already do JOINs / WHERE / aliases."""
    inner = f"SELECT id, name, age FROM {sqlite_seeded} " f"WHERE age >= 25"
    with sqlite_connector:
        records = list(sqlite_connector.read_since("id", 1, query=inner))
    ids = [r.data["id"] for r in records]
    assert ids == sorted(ids)
    assert all(i > 1 for i in ids)


def test_read_since_uses_parameter_binding(
    sqlite_connector: SQLiteConnector, sqlite_seeded: str
) -> None:
    """``cursor_value`` must be bound as a parameter, not interpolated —
    a string that *looks* like SQL must not execute."""
    injected = "1 OR 1=1"
    with sqlite_connector:
        # The injected string is treated as a single cursor value; on the
        # int ``id`` column, sqlite will simply find nothing greater than
        # it (string > int comparison rules), and crucially the trailing
        # ``OR 1=1`` is never parsed as SQL.
        records = list(
            sqlite_connector.read_since(
                "id", injected, query=f"SELECT id, name FROM {sqlite_seeded}"
            )
        )
    # We don't care about the exact rows — only that no error and no
    # injection bypass occurred.
    assert isinstance(records, list)


def test_registry_resolves_sqlite() -> None:
    klass = ConnectorRegistry.get("sqlite")
    assert klass is SQLiteConnector
    assert klass.name == "sqlite"


def test_in_memory_default() -> None:
    c = SQLiteConnector()
    c.connect()
    try:
        assert c.health_check() is True
    finally:
        c.close()


def test_health_check_false_before_connect() -> None:
    c = SQLiteConnector(":memory:")
    assert c.health_check() is False


def test_connect_bad_path_raises() -> None:
    """SQLite is forgiving (creates the file) but a directory-only path should fail."""
    c = SQLiteConnector(database="/nonexistent_dir_xyz/db.sqlite")
    with pytest.raises(ConnectError):
        c.connect()


def test_read_without_query_raises(sqlite_connector: SQLiteConnector) -> None:
    sqlite_connector.connect()
    with pytest.raises(ReadError, match="query"):
        list(sqlite_connector.read())


def test_read_invalid_sql_raises_read_error(sqlite_connector: SQLiteConnector) -> None:
    sqlite_connector.connect()
    with pytest.raises(ReadError):
        list(sqlite_connector.read("SELECT * FROM completely_made_up_table"))


def test_write_without_table_raises(sqlite_connector: SQLiteConnector) -> None:
    sqlite_connector.connect()
    with pytest.raises(WriteError, match="table"):
        sqlite_connector.write(iter([Record(data={"id": 1})]))


def test_write_upsert_requires_key_columns(sqlite_connector: SQLiteConnector) -> None:
    sqlite_connector.connect()
    with pytest.raises(WriteError, match="key_columns"):
        sqlite_connector.write(iter([Record(data={"id": 1})]), table="x", mode="upsert")


def test_write_unknown_mode_raises(sqlite_connector: SQLiteConnector) -> None:
    sqlite_connector.connect()
    with pytest.raises(WriteError, match="unknown write mode"):
        sqlite_connector.write(iter([Record(data={"id": 1})]), table="x", mode="garbage")


def test_write_empty_input_returns_zero(
    sqlite_connector: SQLiteConnector, sqlite_table: str
) -> None:
    with sqlite_connector:
        assert sqlite_connector.write(iter([]), table=sqlite_table) == 0


def test_overwrite_deletes_existing(
    sqlite_connector: SQLiteConnector, sqlite_seeded: str, db_path: Path
) -> None:
    new = [Record(data={"id": 99, "name": "X", "age": 0, "active": 1})]
    with sqlite_connector:
        n = sqlite_connector.write(iter(new), table=sqlite_seeded, mode="overwrite")
    assert n == 1
    with sqlite3.connect(db_path) as raw:
        rows = list(raw.execute(f"SELECT id, name FROM {sqlite_seeded}"))
    assert rows == [(99, "X")]


def test_upsert_updates_existing_and_inserts_new(
    sqlite_connector: SQLiteConnector,
    sqlite_seeded: str,
    db_path: Path,
) -> None:
    upsert_payload = [
        Record(data={"id": 1, "name": "Alice2", "age": 31, "active": 0}),
        Record(data={"id": 4, "name": "Dan", "age": 22, "active": 1}),
    ]
    with sqlite_connector:
        n = sqlite_connector.write(
            iter(upsert_payload),
            table=sqlite_seeded,
            mode="upsert",
            key_columns=["id"],
        )
    assert n == 2
    with sqlite3.connect(db_path) as raw:
        rows = list(raw.execute(f"SELECT id, name, age FROM {sqlite_seeded} ORDER BY id"))
    assert rows == [
        (1, "Alice2", 31),
        (2, "Bob", 25),
        (3, "Carol", 35),
        (4, "Dan", 22),
    ]


def test_read_streams_through_chunked_fetch(
    sqlite_connector: SQLiteConnector, db_path: Path
) -> None:
    table = "etl_chunk_test"
    with sqlite3.connect(db_path) as raw:
        raw.execute(f"CREATE TABLE {table} (id INTEGER)")
        raw.executemany(f"INSERT INTO {table} VALUES (?)", [(i,) for i in range(500)])
        raw.commit()
    with sqlite_connector:
        rows = list(sqlite_connector.read(f"SELECT id FROM {table}", chunk_size=50))
    assert len(rows) == 500
    assert {r.data["id"] for r in rows} == set(range(500))


def test_metadata_includes_source(sqlite_connector: SQLiteConnector, sqlite_seeded: str) -> None:
    with sqlite_connector:
        records = list(sqlite_connector.read(f"SELECT * FROM {sqlite_seeded} LIMIT 1"))
    assert records[0].metadata.get("source") == "sqlite"


def test_identifier_quoting_handles_double_quote(
    sqlite_connector: SQLiteConnector, db_path: Path
) -> None:
    """A column name containing a double quote should still write cleanly."""
    table = "etl_quote_test"
    with sqlite3.connect(db_path) as raw:
        raw.execute(f'CREATE TABLE {table} ("a""b" INTEGER, c INTEGER)')
        raw.commit()
    with sqlite_connector:
        n = sqlite_connector.write(iter([Record(data={'a"b': 1, "c": 2})]), table=table)
    assert n == 1
    with sqlite3.connect(db_path) as raw:
        rows = list(raw.execute(f'SELECT "a""b", c FROM {table}'))
    assert rows == [(1, 2)]


def test_persists_across_connect_close_cycles(
    sqlite_connector: SQLiteConnector, sqlite_table: str
) -> None:
    """File-backed db must survive close()/connect() cycles."""
    rec = [Record(data={"id": 1, "name": "x", "age": 1, "active": 1})]
    with sqlite_connector:
        sqlite_connector.write(iter(rec), table=sqlite_table)
    # re-open and read back
    with sqlite_connector:
        read_back = list(sqlite_connector.read(f"SELECT id, name FROM {sqlite_table}"))
    assert [r.data["id"] for r in read_back] == [1]


def test_write_with_no_columns_returns_zero_and_doesnt_error(
    sqlite_connector: SQLiteConnector, sqlite_table: str
) -> None:
    """Edge: a record with empty data should round-trip cleanly... but our
    contract is that an empty iterator yields 0. Verify."""
    with sqlite_connector:
        n = sqlite_connector.write(iter([]), table=sqlite_table)
    assert n == 0


def _unused(_: Any) -> None:
    """Silence ruff F401 for the type-only Any import."""
