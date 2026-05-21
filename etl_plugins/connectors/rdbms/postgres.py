"""PostgreSQL connector — BatchSource + BatchSink. SPEC.md §6.

Built on psycopg 3 (sync). Optional dependency::

    pip install 'etl-plugins[postgres]'

Modes (``write``):
  * ``append`` (default) — COPY-based bulk insert
  * ``overwrite`` — TRUNCATE + COPY
  * ``upsert`` — INSERT ... ON CONFLICT (``key_columns`` required)

Reads stream rows through a server-side cursor (``itersize=chunk_size``) so
memory usage stays bounded for arbitrarily large result sets.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any
from uuid import uuid4

import psycopg
from psycopg import sql

from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.inspect import ColumnInfo
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


@ConnectorRegistry.register("postgres")
class PostgresConnector(BatchSource, BatchSink):
    """PostgreSQL batch source + sink."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        database: str = "",
        user: str = "",
        password: str = "",
        *,
        sslmode: str = "prefer",
        application_name: str = "etl-plugins",
        connect_timeout: int = 10,
        **extra: Any,
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.sslmode = sslmode
        self.application_name = application_name
        self.connect_timeout = connect_timeout
        # Extra libpq params (e.g. options="-c statement_timeout=...")
        self._extra: dict[str, Any] = extra
        self._conn: psycopg.Connection[tuple[Any, ...]] | None = None

    # ---------- lifecycle ---------------------------------------------------

    def connect(self) -> None:
        if self._conn is not None and not self._conn.closed:
            return
        try:
            self._conn = psycopg.connect(
                host=self.host,
                port=self.port,
                dbname=self.database,
                user=self.user,
                password=self.password,
                sslmode=self.sslmode,
                application_name=self.application_name,
                connect_timeout=self.connect_timeout,
                **self._extra,
            )
        except psycopg.Error as exc:
            raise ConnectError(f"postgres connect failed: {exc}") from exc

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def health_check(self) -> bool:
        if self._conn is None or self._conn.closed:
            return False
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
                row = cur.fetchone()
                return row is not None and row[0] == 1
        except psycopg.Error:
            return False

    @property
    def connection(self) -> psycopg.Connection[tuple[Any, ...]]:
        """Underlying psycopg connection. Raises if not connected.

        Intended for tests / migrations / advanced use — pipeline code should
        use :meth:`read` / :meth:`write`.
        """
        if self._conn is None:
            raise ConnectError("PostgresConnector is not connected")
        return self._conn

    # ---------- SchemaInspector (ADR-0033) ---------------------------------

    def list_tables(self) -> list[str]:
        with self.connection.cursor() as cur:
            cur.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                "ORDER BY table_schema, table_name"
            )
            return [f"{schema}.{name}" for schema, name in cur.fetchall()]

    def list_columns(self, table: str) -> list[ColumnInfo]:
        schema, sep, name = table.rpartition(".")
        if not sep:
            schema, name = "public", table
        with self.connection.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                (schema, name),
            )
            return [ColumnInfo(name=col, type=dtype) for col, dtype in cur.fetchall()]

    # ---------- SqlExecutor (ADR-0035) -------------------------------------

    def execute_statement(self, statement: str) -> int:
        """Run a standalone statement (e.g. DELETE) and commit. Returns rowcount."""
        if self._conn is None:
            raise ConnectError("PostgresConnector is not connected")
        try:
            with self._conn.cursor() as cur:
                cur.execute(statement)
                n = cur.rowcount
            self._conn.commit()
            return int(n)
        except psycopg.Error as exc:
            self._conn.rollback()
            raise WriteError(f"postgres execute_statement failed: {exc}") from exc

    # ---------- BatchSource ------------------------------------------------

    def read(
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]:
        if query is None:
            raise ReadError("PostgresConnector.read requires a SQL query")
        if self._conn is None:
            raise ConnectError("PostgresConnector is not connected")

        # Server-side (named) cursor for memory-bounded streaming.
        cursor_name = str(options.get("cursor_name") or f"etl_{uuid4().hex[:8]}")
        try:
            with self._conn.cursor(name=cursor_name) as cur:
                cur.itersize = chunk_size
                cur.execute(query)
                if cur.description is None:
                    return
                columns = [d.name for d in cur.description]
                for row in cur:
                    yield Record(
                        data=dict(zip(columns, row, strict=False)),
                        metadata={"source": "postgres"},
                    )
        except psycopg.Error as exc:
            raise ReadError(f"postgres read failed: {exc}") from exc

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
        ADR-0024. The cursor column identifier is interpolated via
        ``psycopg.sql.Identifier`` (no SQL injection risk) and the cursor
        value is bound as a server-side parameter.
        """
        if not query:
            raise ReadError(
                "PostgresConnector.read_since requires 'query' (a SELECT exposing cursor_column)"
            )
        if self._conn is None:
            raise ConnectError("PostgresConnector is not connected")

        col = sql.Identifier(cursor_column)
        if cursor_value is None:
            wrapped = sql.SQL("SELECT * FROM ({inner}) AS _inner ORDER BY {col}").format(
                inner=sql.SQL(query),
                col=col,
            )
            params: tuple[Any, ...] = ()
        else:
            wrapped = sql.SQL(
                "SELECT * FROM ({inner}) AS _inner WHERE {col} > %s ORDER BY {col}"
            ).format(inner=sql.SQL(query), col=col)
            params = (cursor_value,)

        cursor_name = str(options.get("cursor_name") or f"etl_{uuid4().hex[:8]}")
        try:
            with self._conn.cursor(name=cursor_name) as cur:
                cur.itersize = chunk_size
                cur.execute(wrapped, params)
                if cur.description is None:
                    return
                columns = [d.name for d in cur.description]
                for row in cur:
                    yield Record(
                        data=dict(zip(columns, row, strict=False)),
                        metadata={"source": "postgres", "cursor_column": cursor_column},
                    )
        except psycopg.Error as exc:
            raise ReadError(f"postgres read_since failed: {exc}") from exc

    # ---------- BatchSink --------------------------------------------------

    def write(
        self,
        records: Iterable[Record],
        *,
        mode: str = "append",
        key_columns: list[str] | None = None,
        table: str | None = None,
        **options: Any,
    ) -> int:
        if self._conn is None:
            raise ConnectError("PostgresConnector is not connected")
        if not table:
            raise WriteError("PostgresConnector.write requires 'table'")
        if mode == "upsert" and not key_columns:
            raise WriteError("mode='upsert' requires non-empty 'key_columns'")
        if mode not in ("append", "overwrite", "upsert"):
            raise WriteError(
                f"unknown write mode: {mode!r} (use 'append', 'overwrite', or 'upsert')"
            )

        it = iter(records)
        first = next(it, None)
        if first is None:
            # 빈 입력. overwrite는 TRUNCATE만 수행해야 의미가 있으나, 안전한 디폴트로 no-op.
            return 0

        columns: list[str] = list(first.data.keys())

        try:
            if mode == "overwrite":
                with self._conn.cursor() as cur:
                    cur.execute(sql.SQL("TRUNCATE TABLE {}").format(_table_ident(table)))

            if mode in ("append", "overwrite"):
                count = self._copy_insert(table, columns, first, it)
            else:  # upsert
                assert key_columns is not None  # 위에서 검증됨
                count = self._upsert(table, columns, key_columns, first, it)

            self._conn.commit()
            return count
        except psycopg.Error as exc:
            self._conn.rollback()
            raise WriteError(f"postgres write failed: {exc}") from exc

    # ---------- internal helpers -------------------------------------------

    def _copy_insert(
        self,
        table: str,
        columns: list[str],
        first: Record,
        rest: Iterator[Record],
    ) -> int:
        assert self._conn is not None
        copy_stmt = sql.SQL("COPY {table} ({cols}) FROM STDIN").format(
            table=_table_ident(table),
            cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
        )
        count = 0
        with self._conn.cursor() as cur, cur.copy(copy_stmt) as copy:
            copy.write_row(tuple(first.data.get(c) for c in columns))
            count = 1
            for record in rest:
                copy.write_row(tuple(record.data.get(c) for c in columns))
                count += 1
        return count

    def _upsert(
        self,
        table: str,
        columns: list[str],
        key_columns: list[str],
        first: Record,
        rest: Iterator[Record],
    ) -> int:
        assert self._conn is not None
        non_key = [c for c in columns if c not in key_columns]
        action: sql.Composable
        if non_key:
            update_set = sql.SQL(", ").join(
                sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in non_key
            )
            action = sql.SQL("DO UPDATE SET ") + update_set
        else:
            action = sql.SQL("DO NOTHING")

        stmt = sql.SQL(
            "INSERT INTO {table} ({cols}) VALUES ({vals}) ON CONFLICT ({keys}) {action}"
        ).format(
            table=_table_ident(table),
            cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
            vals=sql.SQL(", ").join([sql.Placeholder()] * len(columns)),
            keys=sql.SQL(", ").join(map(sql.Identifier, key_columns)),
            action=action,
        )

        count = 0
        with self._conn.cursor() as cur:
            cur.execute(stmt, tuple(first.data.get(c) for c in columns))
            count += 1
            for record in rest:
                cur.execute(stmt, tuple(record.data.get(c) for c in columns))
                count += 1
        return count


def _table_ident(table: str) -> sql.Composed:
    """Quote a possibly schema-qualified table name (e.g. 'public.orders')."""
    parts = table.split(".")
    return sql.SQL(".").join(sql.Identifier(p) for p in parts)
