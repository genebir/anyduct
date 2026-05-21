"""SQLite connector — BatchSource + BatchSink. SPEC.md §6.

Uses the Python stdlib ``sqlite3`` module — no optional dependency required.
Works with both file-based databases (``database="/path/to.db"``) and
in-memory ones (``database=":memory:"``).

Modes (``write``):
  * ``append`` (default) — multi-row INSERT (``executemany``)
  * ``overwrite`` — ``DELETE FROM <table>`` + INSERT
  * ``upsert`` — ``INSERT ... ON CONFLICT (key_columns) DO UPDATE SET ...``
    (requires SQLite 3.24+; ``key_columns`` required).

Reads stream rows via ``cursor.fetchmany(chunk_size)`` so memory usage stays
bounded for large result sets.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Iterator
from typing import Any, Literal, cast

from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.inspect import ColumnInfo
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry

# Identifier guard for PRAGMA table_info (which can't be parameterized).
_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@ConnectorRegistry.register("sqlite")
class SQLiteConnector(BatchSource, BatchSink):
    """SQLite batch source + sink."""

    def __init__(
        self,
        database: str = ":memory:",
        *,
        timeout: float = 5.0,
        isolation_level: str | None = "DEFERRED",
        detect_types: int = 0,
        **extra: Any,
    ) -> None:
        self.database = database
        self.timeout = timeout
        self.isolation_level = isolation_level
        self.detect_types = detect_types
        self._extra: dict[str, Any] = extra
        self._conn: sqlite3.Connection | None = None

    # ---------- lifecycle ---------------------------------------------------

    def connect(self) -> None:
        if self._conn is not None:
            return
        try:
            iso = cast(
                "Literal['DEFERRED', 'EXCLUSIVE', 'IMMEDIATE'] | None",
                self.isolation_level,
            )
            self._conn = sqlite3.connect(
                self.database,
                timeout=self.timeout,
                isolation_level=iso,
                detect_types=self.detect_types,
                **self._extra,
            )
            self._conn.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            raise ConnectError(f"sqlite connect failed: {exc}") from exc

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn = None

    def health_check(self) -> bool:
        if self._conn is None:
            return False
        try:
            row = self._conn.execute("SELECT 1").fetchone()
            return row is not None and row[0] == 1
        except sqlite3.Error:
            return False

    @property
    def connection(self) -> sqlite3.Connection:
        """Underlying sqlite3 connection. Raises if not connected."""
        if self._conn is None:
            raise ConnectError("SQLiteConnector is not connected")
        return self._conn

    # ---------- SchemaInspector (ADR-0033) ---------------------------------

    def list_tables(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]

    def list_columns(self, table: str) -> list[ColumnInfo]:
        if not _SAFE_IDENT.match(table):
            raise ReadError(f"invalid table name for introspection: {table!r}")
        # PRAGMA can't be parameterized; the identifier is validated above.
        rows = self.connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        return [ColumnInfo(name=r["name"], type=r["type"] or "") for r in rows]

    # ---------- BatchSource ------------------------------------------------

    def read(
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]:
        if query is None:
            raise ReadError("SQLiteConnector.read requires a SQL query")
        if self._conn is None:
            raise ConnectError("SQLiteConnector is not connected")

        try:
            cur = self._conn.execute(query)
            try:
                while True:
                    rows = cur.fetchmany(chunk_size)
                    if not rows:
                        return
                    for row in rows:
                        yield Record(
                            data=dict(row),
                            metadata={"source": "sqlite"},
                        )
            finally:
                cur.close()
        except sqlite3.Error as exc:
            raise ReadError(f"sqlite read failed: {exc}") from exc

    # ---------- BatchSource: cursored ---------------------------------------

    def read_since(
        self,
        cursor_column: str,
        cursor_value: Any,
        *,
        query: str | None = None,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]:
        """Read records strictly greater than ``cursor_value`` on ``cursor_column``.

        ``query`` must be a complete SELECT statement that exposes the
        cursor column in its projection (Step 6.1, ADR-0024). The connector
        wraps it as::

            SELECT * FROM (<query>)
            WHERE <cursor_column> > ?
            ORDER BY <cursor_column>

        and binds ``cursor_value`` as a parameter so callers don't have to
        worry about quoting. ``cursor_value=None`` drops the WHERE clause
        and returns every row ordered ascending — the "no progress yet,
        start from the beginning" entry point.
        """
        if not query:
            raise ReadError(
                "SQLiteConnector.read_since requires 'query' (a SELECT exposing cursor_column)"
            )
        if self._conn is None:
            raise ConnectError("SQLiteConnector is not connected")

        col = _ident(cursor_column)
        if cursor_value is None:
            wrapped = f"SELECT * FROM ({query}) ORDER BY {col}"
            params: tuple[Any, ...] = ()
        else:
            wrapped = f"SELECT * FROM ({query}) WHERE {col} > ? ORDER BY {col}"
            params = (cursor_value,)

        try:
            cur = self._conn.execute(wrapped, params)
            try:
                while True:
                    rows = cur.fetchmany(chunk_size)
                    if not rows:
                        return
                    for row in rows:
                        yield Record(
                            data=dict(row),
                            metadata={"source": "sqlite", "cursor_column": cursor_column},
                        )
            finally:
                cur.close()
        except sqlite3.Error as exc:
            raise ReadError(f"sqlite read_since failed: {exc}") from exc

    # ---------- BatchSink --------------------------------------------------

    def write(
        self,
        records: Iterable[Record],
        *,
        mode: str = "append",
        key_columns: list[str] | None = None,
        table: str | None = None,
        batch_size: int = 1000,
        **options: Any,
    ) -> int:
        if self._conn is None:
            raise ConnectError("SQLiteConnector is not connected")
        if not table:
            raise WriteError("SQLiteConnector.write requires 'table'")
        if mode == "upsert" and not key_columns:
            raise WriteError("mode='upsert' requires non-empty 'key_columns'")
        if mode not in ("append", "overwrite", "upsert"):
            raise WriteError(
                f"unknown write mode: {mode!r} (use 'append', 'overwrite', or 'upsert')"
            )

        it = iter(records)
        first = next(it, None)
        if first is None:
            return 0

        columns: list[str] = list(first.data.keys())

        try:
            if mode == "overwrite":
                self._conn.execute(f"DELETE FROM {_table_ident(table)}")

            if mode == "upsert":
                assert key_columns is not None
                count = self._upsert(table, columns, key_columns, first, it, batch_size)
            else:
                count = self._bulk_insert(table, columns, first, it, batch_size)

            self._conn.commit()
            return count
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise WriteError(f"sqlite write failed: {exc}") from exc

    # ---------- internal helpers -------------------------------------------

    def _bulk_insert(
        self,
        table: str,
        columns: list[str],
        first: Record,
        rest: Iterator[Record],
        batch_size: int,
    ) -> int:
        assert self._conn is not None
        col_list = ", ".join(_ident(c) for c in columns)
        placeholders = ", ".join(["?"] * len(columns))
        stmt = f"INSERT INTO {_table_ident(table)} ({col_list}) VALUES ({placeholders})"

        count = 0
        buf: list[tuple[Any, ...]] = [tuple(first.data.get(c) for c in columns)]
        for record in rest:
            buf.append(tuple(record.data.get(c) for c in columns))
            if len(buf) >= batch_size:
                self._conn.executemany(stmt, buf)
                count += len(buf)
                buf.clear()
        if buf:
            self._conn.executemany(stmt, buf)
            count += len(buf)
        return count

    def _upsert(
        self,
        table: str,
        columns: list[str],
        key_columns: list[str],
        first: Record,
        rest: Iterator[Record],
        batch_size: int,
    ) -> int:
        assert self._conn is not None
        non_key = [c for c in columns if c not in key_columns]
        col_list = ", ".join(_ident(c) for c in columns)
        placeholders = ", ".join(["?"] * len(columns))
        key_list = ", ".join(_ident(k) for k in key_columns)
        if non_key:
            update_clause = ", ".join(f"{_ident(c)} = excluded.{_ident(c)}" for c in non_key)
            action = f"DO UPDATE SET {update_clause}"
        else:
            action = "DO NOTHING"
        stmt = (
            f"INSERT INTO {_table_ident(table)} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT ({key_list}) {action}"
        )

        count = 0
        buf: list[tuple[Any, ...]] = [tuple(first.data.get(c) for c in columns)]
        for record in rest:
            buf.append(tuple(record.data.get(c) for c in columns))
            if len(buf) >= batch_size:
                self._conn.executemany(stmt, buf)
                count += len(buf)
                buf.clear()
        if buf:
            self._conn.executemany(stmt, buf)
            count += len(buf)
        return count


def _ident(name: str) -> str:
    """Quote an SQLite identifier with double-quotes, escaping any embedded quotes."""
    return '"' + name.replace('"', '""') + '"'


def _table_ident(table: str) -> str:
    """Quote a possibly schema-qualified table name (e.g. 'main.orders')."""
    parts = table.split(".")
    return ".".join(_ident(p) for p in parts)
