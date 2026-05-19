"""MySQL / MariaDB connector — BatchSource + BatchSink. SPEC.md §6.

Built on PyMySQL (pure Python). Optional dependency::

    pip install 'etl-plugins[mysql]'

Modes (``write``):
  * ``append`` (default) — multi-row INSERT (``executemany``)
  * ``overwrite`` — TRUNCATE + INSERT
  * ``upsert`` — INSERT ... ON DUPLICATE KEY UPDATE (``key_columns`` required
    to identify which columns are the dedup key — used for the *exception
    message* when the table lacks a matching unique key, since MySQL relies
    on the table's declared keys for the actual ON DUPLICATE behavior).

Reads stream rows through PyMySQL's server-side ``SSDictCursor`` so memory
usage stays bounded for arbitrarily large result sets.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import pymysql
from pymysql.cursors import SSDictCursor

from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


@ConnectorRegistry.register("mysql")
class MySQLConnector(BatchSource, BatchSink):
    """MySQL / MariaDB batch source + sink."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        database: str = "",
        user: str = "",
        password: str = "",
        *,
        charset: str = "utf8mb4",
        connect_timeout: int = 10,
        ssl: dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.charset = charset
        self.connect_timeout = connect_timeout
        self.ssl = ssl
        # Any extra PyMySQL kwargs (e.g. local_infile=True for LOAD DATA optimization).
        self._extra: dict[str, Any] = extra
        self._conn: pymysql.connections.Connection | None = None

    # ---------- lifecycle ---------------------------------------------------

    def connect(self) -> None:
        if self._conn is not None and self._conn.open:
            return
        try:
            self._conn = pymysql.connect(
                host=self.host,
                port=self.port,
                database=self.database or None,
                user=self.user,
                password=self.password,
                charset=self.charset,
                connect_timeout=self.connect_timeout,
                ssl=self.ssl,
                autocommit=False,
                **self._extra,
            )
        except pymysql.MySQLError as exc:
            raise ConnectError(f"mysql connect failed: {exc}") from exc

    def close(self) -> None:
        if self._conn is not None and self._conn.open:
            self._conn.close()
        self._conn = None

    def health_check(self) -> bool:
        if self._conn is None or not self._conn.open:
            return False
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
                row = cur.fetchone()
                return row is not None and row[0] == 1
        except pymysql.MySQLError:
            return False

    @property
    def connection(self) -> pymysql.connections.Connection:
        """Underlying PyMySQL connection. Raises if not connected."""
        if self._conn is None or not self._conn.open:
            raise ConnectError("MySQLConnector is not connected")
        return self._conn

    # ---------- BatchSource ------------------------------------------------

    def read(
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]:
        if query is None:
            raise ReadError("MySQLConnector.read requires a SQL query")
        if self._conn is None or not self._conn.open:
            raise ConnectError("MySQLConnector is not connected")

        try:
            cur = self._conn.cursor(SSDictCursor)
            try:
                cur.execute(query)
                while True:
                    rows = cur.fetchmany(chunk_size)
                    if not rows:
                        return
                    for row in rows:
                        yield Record(data=dict(row), metadata={"source": "mysql"})
            finally:
                cur.close()
        except pymysql.MySQLError as exc:
            raise ReadError(f"mysql read failed: {exc}") from exc

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

        Wraps the user's SELECT as a subquery + WHERE + ORDER BY identical
        in spirit to :meth:`SQLiteConnector.read_since` — see Step 6.1 /
        ADR-0024. The cursor column identifier is quoted via ``_ident``
        (backticks) and the cursor value is bound as a server-side
        parameter, so neither path is injection-prone.
        """
        if not query:
            raise ReadError(
                "MySQLConnector.read_since requires 'query' (a SELECT exposing cursor_column)"
            )
        if self._conn is None or not self._conn.open:
            raise ConnectError("MySQLConnector is not connected")

        col = _ident(cursor_column)
        if cursor_value is None:
            wrapped = f"SELECT * FROM ({query}) AS _inner ORDER BY {col}"
            params: tuple[Any, ...] = ()
        else:
            wrapped = f"SELECT * FROM ({query}) AS _inner WHERE {col} > %s ORDER BY {col}"
            params = (cursor_value,)

        try:
            cur = self._conn.cursor(SSDictCursor)
            try:
                cur.execute(wrapped, params)
                while True:
                    rows = cur.fetchmany(chunk_size)
                    if not rows:
                        return
                    for row in rows:
                        yield Record(
                            data=dict(row),
                            metadata={"source": "mysql", "cursor_column": cursor_column},
                        )
            finally:
                cur.close()
        except pymysql.MySQLError as exc:
            raise ReadError(f"mysql read_since failed: {exc}") from exc

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
        if self._conn is None or not self._conn.open:
            raise ConnectError("MySQLConnector is not connected")
        if not table:
            raise WriteError("MySQLConnector.write requires 'table'")
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
                with self._conn.cursor() as cur:
                    cur.execute(f"TRUNCATE TABLE {_table_ident(table)}")

            if mode == "upsert":
                assert key_columns is not None
                count = self._upsert(table, columns, key_columns, first, it, batch_size)
            else:  # append or overwrite
                count = self._bulk_insert(table, columns, first, it, batch_size)

            self._conn.commit()
            return count
        except pymysql.MySQLError as exc:
            self._conn.rollback()
            raise WriteError(f"mysql write failed: {exc}") from exc

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
        placeholders = ", ".join(["%s"] * len(columns))
        stmt = f"INSERT INTO {_table_ident(table)} ({col_list}) VALUES ({placeholders})"

        count = 0
        with self._conn.cursor() as cur:
            buf: list[tuple[Any, ...]] = [tuple(first.data.get(c) for c in columns)]
            for record in rest:
                buf.append(tuple(record.data.get(c) for c in columns))
                if len(buf) >= batch_size:
                    cur.executemany(stmt, buf)
                    count += len(buf)
                    buf.clear()
            if buf:
                cur.executemany(stmt, buf)
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
        placeholders = ", ".join(["%s"] * len(columns))
        if non_key:
            update_clause = ", ".join(f"{_ident(c)} = VALUES({_ident(c)})" for c in non_key)
            action = f"ON DUPLICATE KEY UPDATE {update_clause}"
        else:
            # No non-key columns to update — degenerate, but valid: turn into a no-op upsert.
            # The dummy "k = VALUES(k)" preserves the row's existing values.
            update_clause = ", ".join(f"{_ident(c)} = VALUES({_ident(c)})" for c in key_columns)
            action = f"ON DUPLICATE KEY UPDATE {update_clause}"

        stmt = f"INSERT INTO {_table_ident(table)} ({col_list}) VALUES ({placeholders}) {action}"

        count = 0
        with self._conn.cursor() as cur:
            buf: list[tuple[Any, ...]] = [tuple(first.data.get(c) for c in columns)]
            for record in rest:
                buf.append(tuple(record.data.get(c) for c in columns))
                if len(buf) >= batch_size:
                    cur.executemany(stmt, buf)
                    count += len(buf)
                    buf.clear()
            if buf:
                cur.executemany(stmt, buf)
                count += len(buf)
        return count


def _ident(name: str) -> str:
    """Quote a single identifier, escaping any embedded backticks."""
    return "`" + name.replace("`", "``") + "`"


def _table_ident(table: str) -> str:
    """Quote a possibly database-qualified table name (e.g. 'db.orders')."""
    parts = table.split(".")
    return ".".join(_ident(p) for p in parts)
