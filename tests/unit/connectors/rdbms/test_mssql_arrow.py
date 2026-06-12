"""MSSQL Arrow fast-path units (ADR-0093, 2026-06-12).

Fake-cursor pattern (no SQL Server testcontainer in unit scope — the
vertica/DW convention). pymssql's DBAPI type codes are coarse buckets,
so unlike vertica/mysql only STRING/BINARY pin up front; the rest is
infer-from-first-chunk-then-lock, which these tests nail down.
"""

from __future__ import annotations

from typing import Any

import pytest

pa = pytest.importorskip("pyarrow")

from etl_plugins.connectors.rdbms.mssql import (  # noqa: E402
    MSSQLConnector,
    _arrow_type_for_mssql,
)
from etl_plugins.core.arrow import Partition  # noqa: E402
from etl_plugins.core.exceptions import ReadError, WriteError  # noqa: E402

# pymssql DBAPI bucket codes.
STRING, BINARY, NUMBER, DATETIME, DECIMAL = 1, 2, 3, 4, 5


def _desc(name: str, code: int) -> tuple:
    return (name, code, None, None, None, None, True)


class _FakeCursor:
    def __init__(self, description: list[tuple], chunks: list[list[tuple]]) -> None:
        self.description = description
        self._chunks = list(chunks)
        self.executed: list[tuple[str, Any]] = []
        self.executemany_calls: list[tuple[str, list[tuple]]] = []
        self.closed = False

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        self.executemany_calls.append((sql, list(rows)))

    def fetchmany(self, n: int) -> list[tuple]:
        return self._chunks.pop(0) if self._chunks else []

    def close(self) -> None:
        self.closed = True


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.committed = 0
        self.rolled_back = 0

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1


def _connector(cursor: _FakeCursor) -> MSSQLConnector:
    c = MSSQLConnector(host="x", database="d", user="u", password="p")
    c._conn = _FakeConn(cursor)
    return c


# ---------- read_arrow --------------------------------------------------


def test_read_arrow_pins_string_infers_number() -> None:
    """NUMBER (int/float/bool bucket) must infer from the first chunk and
    stay locked; STRING pins immediately."""
    cur = _FakeCursor(
        description=[_desc("id", NUMBER), _desc("name", STRING)],
        chunks=[[(1, "a")], [(2, "b")]],
    )
    batches = list(_connector(cur).read_arrow(query="SELECT * FROM t"))
    assert len(batches) == 2
    assert batches[0].schema.field("name").type == pa.string()
    # int values → int64 inferred, second chunk keeps the same type.
    assert batches[0].schema.field("id").type == batches[1].schema.field("id").type
    assert batches[1].column(0).to_pylist() == [2]


def test_read_arrow_partition_predicate_parameterised() -> None:
    cur = _FakeCursor(description=[_desc("id", NUMBER)], chunks=[[(5,)]])
    list(
        _connector(cur).read_arrow(
            query="SELECT id FROM t",
            partition=Partition(column="id", lower=10, upper=20),
        )
    )
    sql, params = cur.executed[0]
    assert "WHERE [id] > %s AND [id] <= %s" in sql
    assert params == (10, 20)


def test_read_arrow_requires_query() -> None:
    cur = _FakeCursor(description=[], chunks=[])
    with pytest.raises(ReadError):
        list(_connector(cur).read_arrow())


# ---------- write_arrow -------------------------------------------------


def _batch(rows: list[dict[str, Any]]) -> pa.RecordBatch:
    return pa.RecordBatch.from_pylist(rows)


def test_write_arrow_append_executemany() -> None:
    cur = _FakeCursor(description=[], chunks=[])
    conn = _connector(cur)
    n = conn.write_arrow([_batch([{"id": 1, "name": "a"}])], table="s.t", mode="append")
    assert n == 1
    sql, rows = cur.executemany_calls[0]
    assert sql == "INSERT INTO [s].[t] ([id], [name]) VALUES (%s, %s)"
    assert rows == [(1, "a")]
    assert conn._conn.committed == 1  # type: ignore[union-attr]


def test_write_arrow_overwrite_truncates_or_deletes() -> None:
    cur = _FakeCursor(description=[], chunks=[])
    _connector(cur).write_arrow([_batch([{"id": 1}])], table="s.t", mode="overwrite")
    assert any("TRUNCATE TABLE [s].[t]" in sql for sql, _ in cur.executed)

    cur2 = _FakeCursor(description=[], chunks=[])
    _connector(cur2).write_arrow(
        [_batch([{"id": 1}])], table="s.t", mode="overwrite", overwrite_strategy="delete"
    )
    assert any("DELETE FROM [s].[t]" in sql for sql, _ in cur2.executed)


def test_write_arrow_rejects_upsert() -> None:
    cur = _FakeCursor(description=[], chunks=[])
    with pytest.raises(WriteError):
        _connector(cur).write_arrow([], table="s.t", mode="upsert")


def test_write_arrow_error_rolls_back() -> None:
    class _Boom(_FakeCursor):
        def executemany(self, sql: str, rows: list[tuple]) -> None:
            raise RuntimeError("disk full")

    cur = _Boom(description=[], chunks=[])
    conn = _connector(cur)
    with pytest.raises(WriteError):
        conn.write_arrow([_batch([{"id": 1}])], table="s.t", mode="append")
    assert conn._conn.rolled_back == 1  # type: ignore[union-attr]


# ---------- type map ----------------------------------------------------


def test_type_map_only_string_binary_pin() -> None:
    assert _arrow_type_for_mssql(_desc("s", STRING)) == pa.string()
    assert _arrow_type_for_mssql(_desc("b", BINARY)) == pa.binary()
    # Coarse buckets stay None → infer-then-lock.
    assert _arrow_type_for_mssql(_desc("n", NUMBER)) is None
    assert _arrow_type_for_mssql(_desc("d", DATETIME)) is None
    assert _arrow_type_for_mssql(_desc("x", DECIMAL)) is None
