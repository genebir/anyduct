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

import re
from collections.abc import Iterable, Iterator
from typing import Any

import pymysql
from pymysql.cursors import SSDictCursor

from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.inspect import ColumnInfo
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry

# DDL identifier whitelist — identifiers don't accept parameterised
# placeholders, so we validate before string interpolation.
_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@ConnectorRegistry.register("mysql")
class MySQLConnector(BatchSource, BatchSink):
    """MySQL / MariaDB batch source + sink."""

    # Same-connection pushdown (ADR-0093 P2c): this dialect supports
    # ``INSERT INTO <table> <select>`` so source==sink pipelines can run
    # entirely inside the database (no data movement).
    supports_sql_pushdown = True

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

    # ---------- SchemaInspector (ADR-0033) ---------------------------------

    def list_tables(self) -> list[str]:
        with self.connection.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() ORDER BY table_name"
            )
            return [row[0] for row in cur.fetchall()]

    def list_columns(self, table: str) -> list[ColumnInfo]:
        with self.connection.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s ORDER BY ordinal_position",
                (table,),
            )
            return [ColumnInfo(name=col, type=dtype) for col, dtype in cur.fetchall()]

    # ---------- SchemaWriter (Phase VV / ADR-0066, 2026-05-29) -------------

    def ensure_table(
        self,
        table: str,
        columns: list[ColumnInfo],
        *,
        if_exists: str = "skip",  # "skip" | "drop" | "error"
        primary_key: list[str] | None = None,
    ) -> None:
        """Create ``table`` from ``columns`` if it doesn't already exist.

        Vendor type strings get normalised through
        :mod:`etl_plugins.core.type_mapping` and rendered in mysql's
        vocabulary — postgres ``BIGINT`` stays ``BIGINT``, sqlite
        ``INTEGER`` becomes ``INT``, ``TIMESTAMPTZ`` becomes ``DATETIME``,
        etc.

        ``primary_key`` (Phase AAC, ADR-0072) — when supplied, emits a
        ``PRIMARY KEY (...)`` table constraint. Required for upsert
        targets so ``INSERT ... ON DUPLICATE KEY UPDATE`` has a unique
        index to attach to.
        """
        from etl_plugins.core.type_mapping import normalize_db_type, render_canonical

        if self._conn is None or not self._conn.open:
            raise ConnectError("MySQLConnector is not connected")
        if not _SAFE_IDENT.match(table):
            raise WriteError(f"invalid table name for ensure_table: {table!r}")
        if not columns:
            raise WriteError(f"ensure_table({table!r}) requires a non-empty column list")

        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (table,),
            )
            already_exists = cur.fetchone() is not None
        if already_exists:
            if if_exists == "skip":
                return
            if if_exists == "error":
                raise WriteError(f"table {table!r} already exists")
            if if_exists == "drop":
                with self._conn.cursor() as cur:
                    cur.execute(f"DROP TABLE `{table}`")
                self._conn.commit()
        elif if_exists not in {"skip", "drop", "error"}:
            raise WriteError(
                f"ensure_table: unknown if_exists={if_exists!r} " "(use 'skip', 'drop', or 'error')"
            )

        col_names = {c.name for c in columns}
        col_fragments: list[str] = []
        for c in columns:
            if not _SAFE_IDENT.match(c.name):
                raise WriteError(f"ensure_table: invalid column name {c.name!r}")
            spec = normalize_db_type(c.type or "")
            mysql_type = render_canonical(spec, dialect="mysql")
            col_fragments.append(f"`{c.name}` {mysql_type}")
        if primary_key:
            for k in primary_key:
                if not _SAFE_IDENT.match(k):
                    raise WriteError(f"ensure_table: invalid primary key column {k!r}")
                if k not in col_names:
                    raise WriteError(f"ensure_table: primary key column {k!r} not in columns")
            pk_list = ", ".join(f"`{k}`" for k in primary_key)
            col_fragments.append(f"PRIMARY KEY ({pk_list})")
        ddl = f"CREATE TABLE `{table}` ({', '.join(col_fragments)})"
        with self._conn.cursor() as cur:
            cur.execute(ddl)
        self._conn.commit()

    # ---------- SqlExecutor (ADR-0035) -------------------------------------

    def execute_statement(self, statement: str) -> int:
        """Run a standalone statement (e.g. DELETE) and commit. Returns rowcount."""
        if self._conn is None or not self._conn.open:
            raise ConnectError("MySQLConnector is not connected")
        try:
            with self._conn.cursor() as cur:
                n = cur.execute(statement)
            self._conn.commit()
            return int(n)
        except pymysql.MySQLError as exc:
            self._conn.rollback()
            raise WriteError(f"mysql execute_statement failed: {exc}") from exc

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
        pre_sql: str | None = None,
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
        # ``pre_sql`` (ADR-0035 atomic variant) runs as the first statement in
        # the write transaction so a DELETE + the insert commit together. Use
        # DELETE (DML, transactional in InnoDB) — TRUNCATE is DDL and would
        # implicitly commit, breaking atomicity on MySQL.
        if first is None and not pre_sql:
            return 0

        try:
            if pre_sql:
                with self._conn.cursor() as cur:
                    cur.execute(pre_sql)
            if first is None:
                self._conn.commit()
                return 0

            columns: list[str] = list(first.data.keys())
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
