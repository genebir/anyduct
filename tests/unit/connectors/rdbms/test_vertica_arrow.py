"""Vertica Arrow fast-path units (ADR-0093, 2026-06-12).

Fake-cursor pattern (the DW convention — no Vertica testcontainer): we
validate the columnar assembly, type pinning, partition predicate, and
write SQL against a scripted cursor. Live-server verification is gated
on a real Vertica, like the rest of the vertica surface.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

pa = pytest.importorskip("pyarrow")

from etl_plugins.connectors.rdbms.vertica import (  # noqa: E402
    VerticaConnector,
    _arrow_type_for_vertica,
)
from etl_plugins.core.arrow import Partition  # noqa: E402
from etl_plugins.core.exceptions import ReadError, WriteError  # noqa: E402

# vertica_python.datatypes.VerticaType codes (pinned constants so the
# test reads without the driver docs open).
BOOL, INT8, FLOAT8, CHAR, VARCHAR = 5, 6, 7, 8, 9
DATE, TIME, TIMESTAMP, TIMESTAMPTZ, NUMERIC = 10, 11, 12, 13, 16


def _desc(name: str, code: int, precision: int | None = None, scale: int | None = None) -> tuple:
    return (name, code, None, None, precision, scale, True)


class _FakeCursor:
    """Scripted DBAPI cursor: one execute, chunked fetchmany, records SQL."""

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


def _connector(cursor: _FakeCursor) -> VerticaConnector:
    c = VerticaConnector(host="x", database="d", user="u", password="p")
    c._conn = _FakeConn(cursor)
    return c


# ---------- read_arrow --------------------------------------------------


def test_read_arrow_assembles_typed_batches() -> None:
    cur = _FakeCursor(
        description=[_desc("id", INT8), _desc("name", VARCHAR), _desc("ok", BOOL)],
        chunks=[[(1, "a", True), (2, "b", False)], [(3, "c", None)]],
    )
    batches = list(_connector(cur).read_arrow(query="SELECT * FROM t"))
    assert len(batches) == 2
    assert batches[0].schema.field("id").type == pa.int64()
    assert batches[0].schema.field("name").type == pa.string()
    assert batches[0].schema.field("ok").type == pa.bool_()
    assert batches[0].column(0).to_pylist() == [1, 2]
    assert batches[1].column(1).to_pylist() == ["c"]
    assert cur.closed


def test_read_arrow_numeric_pins_declared_precision() -> None:
    """NUMERIC must use the DECLARED (p, s) — first-chunk inference breaks
    the moment a later chunk has more integer digits (mysql lesson)."""
    cur = _FakeCursor(
        description=[_desc("amt", NUMERIC, precision=12, scale=2)],
        chunks=[[(Decimal("1.50"),)], [(Decimal("9876543210.99"),)]],
    )
    batches = list(_connector(cur).read_arrow(query="SELECT amt FROM t"))
    assert all(b.schema.field("amt").type == pa.decimal128(12, 2) for b in batches)
    assert batches[1].column(0).to_pylist() == [Decimal("9876543210.99")]


def test_read_arrow_partition_predicate_parameterised() -> None:
    cur = _FakeCursor(description=[_desc("id", INT8)], chunks=[[(5,)]])
    list(
        _connector(cur).read_arrow(
            query="SELECT id FROM t",
            partition=Partition(column="id", lower=10, upper=20),
        )
    )
    sql, params = cur.executed[0]
    assert 'WHERE "id" > %s AND "id" <= %s' in sql
    assert params == (10, 20)


def test_read_arrow_requires_query() -> None:
    cur = _FakeCursor(description=[], chunks=[])
    with pytest.raises(ReadError):
        list(_connector(cur).read_arrow())


def test_read_arrow_ambiguous_type_inferred_then_locked() -> None:
    """TIMESTAMPTZ has no pinned type (tz comes from the values) — the
    first chunk's inferred type must be frozen for later chunks."""
    from datetime import UTC, datetime

    ts = datetime(2026, 6, 12, tzinfo=UTC)
    cur = _FakeCursor(
        description=[_desc("at", TIMESTAMPTZ)],
        chunks=[[(ts,)], [(ts,)]],
    )
    batches = list(_connector(cur).read_arrow(query="SELECT at FROM t"))
    assert batches[0].schema.field("at").type == batches[1].schema.field("at").type


# ---------- write_arrow -------------------------------------------------


def _batch(rows: list[dict[str, Any]]) -> pa.RecordBatch:
    return pa.RecordBatch.from_pylist(rows)


def test_write_arrow_append_executemany() -> None:
    cur = _FakeCursor(description=[], chunks=[])
    conn = _connector(cur)
    n = conn.write_arrow(
        [_batch([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])],
        table="s.t",
        mode="append",
    )
    assert n == 2
    sql, rows = cur.executemany_calls[0]
    assert sql == 'INSERT INTO "s"."t" ("id", "name") VALUES (%s, %s)'
    assert rows == [(1, "a"), (2, "b")]
    assert conn._conn.committed == 1  # type: ignore[union-attr]


def test_write_arrow_overwrite_deletes_first() -> None:
    cur = _FakeCursor(description=[], chunks=[])
    _connector(cur).write_arrow([_batch([{"id": 1}])], table="s.t", mode="overwrite")
    assert any('DELETE FROM "s"."t"' in sql for sql, _ in cur.executed)


def test_write_arrow_pre_sql_runs_first_in_transaction() -> None:
    cur = _FakeCursor(description=[], chunks=[])
    _connector(cur).write_arrow(
        [_batch([{"id": 1}])], table="s.t", mode="append", pre_sql="DELETE FROM s.t WHERE d='x'"
    )
    assert cur.executed[0][0] == "DELETE FROM s.t WHERE d='x'"


def test_write_arrow_rejects_upsert() -> None:
    cur = _FakeCursor(description=[], chunks=[])
    with pytest.raises(WriteError):
        _connector(cur).write_arrow([], table="s.t", mode="upsert")


def test_write_arrow_schema_drift_reorders_or_fails() -> None:
    cur = _FakeCursor(description=[], chunks=[])
    n = _connector(cur).write_arrow(
        [
            _batch([{"id": 1, "name": "a"}]),
            # Same columns, different order — must reorder, not corrupt.
            pa.RecordBatch.from_pylist([{"name": "b", "id": 2}]).select(["name", "id"]),
        ],
        table="s.t",
        mode="append",
    )
    assert n == 2
    assert cur.executemany_calls[1][1] == [(2, "b")]


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


def test_type_map_pins_unambiguous_codes() -> None:
    assert _arrow_type_for_vertica(_desc("b", BOOL)) == pa.bool_()
    assert _arrow_type_for_vertica(_desc("i", INT8)) == pa.int64()
    assert _arrow_type_for_vertica(_desc("f", FLOAT8)) == pa.float64()
    assert _arrow_type_for_vertica(_desc("s", VARCHAR)) == pa.string()
    assert _arrow_type_for_vertica(_desc("d", DATE)) == pa.date32()
    assert _arrow_type_for_vertica(_desc("t", TIMESTAMP)) == pa.timestamp("us")
    assert _arrow_type_for_vertica(_desc("n", NUMERIC, 10, 2)) == pa.decimal128(10, 2)
    # Ambiguous → None (infer from first chunk then lock).
    assert _arrow_type_for_vertica(_desc("tz", TIMESTAMPTZ)) is None
    assert _arrow_type_for_vertica(_desc("tm", TIME)) is None
