"""ClickHouse connector — BatchSource + BatchSink (Phase AGH, ADR-0080).

ClickHouse is a column-oriented OLAP database. Built on the official
``clickhouse-connect`` client's DB-API. Optional dependency::

    pip install 'etl-plugins[clickhouse]'

Modes (``write``):

* ``append`` (default) — multi-row ``INSERT`` (one statement per batch)
* ``overwrite`` — ``TRUNCATE TABLE`` + ``INSERT``
* ``upsert`` — **not supported**: ClickHouse is append-optimized and has
  no row-level UPSERT. A clear :class:`WriteError` is raised pointing at
  the ``ReplacingMergeTree`` pattern.

``ensure_table`` creates a ``MergeTree`` table — ClickHouse requires an
engine + sorting key, so the auto-created table uses ``ENGINE =
MergeTree`` with ``ORDER BY`` the primary key (or ``tuple()`` when none
is given). Identifiers are backtick-quoted. ClickHouse has no
transactions, so there is no commit/rollback.

The driver is imported **lazily** inside :meth:`connect`.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable, Iterator
from typing import Any

from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.inspect import ColumnInfo
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_QUALIFIED_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")

# Wrappers ClickHouse puts around a base type in ``system.columns.type``.
# We strip them so the canonical translator sees the underlying type.
_CH_WRAPPERS = ("Nullable", "LowCardinality")


def _q(ident: str) -> str:
    """Backtick-quote a single identifier (ClickHouse standard)."""
    if not _SAFE_IDENT.match(ident):
        raise WriteError(f"unsafe identifier: {ident!r}")
    return f"`{ident}`"


def _unwrap_ch_type(raw: str) -> str:
    """Strip ``Nullable(...)`` / ``LowCardinality(...)`` wrappers so the
    inner type reaches :func:`normalize_db_type` (e.g.
    ``Nullable(Int64)`` → ``Int64``, ``LowCardinality(String)`` →
    ``String``)."""
    t = raw.strip()
    changed = True
    while changed:
        changed = False
        for wrapper in _CH_WRAPPERS:
            prefix = f"{wrapper}("
            if t.startswith(prefix) and t.endswith(")"):
                t = t[len(prefix) : -1].strip()
                changed = True
    return t


@ConnectorRegistry.register("clickhouse")
class ClickHouseConnector(BatchSource, BatchSink):
    """ClickHouse batch source + sink (column-oriented OLAP)."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8123,
        database: str = "default",
        user: str = "default",
        password: str = "",
        **extra: Any,
    ) -> None:
        self.host = host
        self.port = port
        self.database = database or "default"
        self.user = user
        self.password = password
        self._extra: dict[str, Any] = extra
        self._conn: Any = None

    # ---------- table-name quoting -----------------------------------------

    def _qt(self, table: str) -> str:
        """Backtick-quote a table path; a bare name is qualified with the
        connector's ``database`` so writes don't depend on the session's
        current database."""
        if not _SAFE_QUALIFIED_IDENT.match(table):
            raise WriteError(f"unsafe table name: {table!r}")
        path = table if "." in table else f"{self.database}.{table}"
        return ".".join(f"`{p}`" for p in path.split("."))

    # ---------- lifecycle ---------------------------------------------------

    def connect(self) -> None:
        if self._conn is not None:
            return
        try:
            from clickhouse_connect import dbapi
        except ImportError as exc:  # pragma: no cover - import side effect
            raise ConnectError(
                "clickhouse-connect not installed. Install with: "
                "pip install 'etl-plugins[clickhouse]'"
            ) from exc
        try:
            self._conn = dbapi.connect(
                host=self.host,
                port=self.port,
                username=self.user,
                password=self.password,
                database=self.database,
                **self._extra,
            )
        except Exception as exc:  # clickhouse_connect errors are broad
            raise ConnectError(f"clickhouse connect failed: {exc}") from exc

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
        self._conn = None

    def health_check(self) -> bool:
        if self._conn is None:
            return False
        try:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT 1")
                row = cur.fetchone()
                return row is not None and row[0] == 1
            finally:
                cur.close()
        except Exception:
            return False

    @property
    def connection(self) -> Any:
        if self._conn is None:
            raise ConnectError("ClickHouseConnector is not connected")
        return self._conn

    # ---------- SchemaInspector (ADR-0033) ---------------------------------

    def list_tables(self) -> list[str]:
        cur = self.connection.cursor()
        try:
            cur.execute(
                "SELECT database, name FROM system.tables "
                "WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') "
                "ORDER BY database, name"
            )
            rows = cur.fetchall()
        finally:
            cur.close()
        return [f"{db}.{name}" for db, name in rows]

    def list_columns(self, table: str) -> list[ColumnInfo]:
        db, sep, name = table.rpartition(".")
        if not sep:
            db, name = self.database, table
        cur = self.connection.cursor()
        try:
            cur.execute(
                "SELECT name, type FROM system.columns "
                "WHERE database = %s AND table = %s ORDER BY position",
                (db, name),
            )
            rows = cur.fetchall()
        finally:
            cur.close()
        # Strip Nullable()/LowCardinality() so the canonical translator
        # sees the base type.
        return [ColumnInfo(name=col, type=_unwrap_ch_type(str(dtype))) for col, dtype in rows]

    # ---------- SchemaWriter (Phase VV / ADR-0066, Phase AAC PK) -----------

    def ensure_table(
        self,
        table: str,
        columns: list[ColumnInfo],
        *,
        if_exists: str = "skip",
        primary_key: list[str] | None = None,
    ) -> None:
        from etl_plugins.core.type_mapping import normalize_db_type, render_canonical

        if not _SAFE_QUALIFIED_IDENT.match(table):
            raise WriteError(f"invalid table name for ensure_table: {table!r}")
        if not columns:
            raise WriteError(f"ensure_table({table!r}) requires a non-empty column list")
        if if_exists not in {"skip", "drop", "error"}:
            raise WriteError(
                f"ensure_table: unknown if_exists={if_exists!r} (use 'skip', 'drop', or 'error')"
            )

        db, sep, name = table.rpartition(".")
        if not sep:
            db, name = self.database, table

        cur = self.connection.cursor()
        try:
            cur.execute(
                "SELECT 1 FROM system.tables WHERE database = %s AND name = %s",
                (db, name),
            )
            already = cur.fetchone() is not None
            if already:
                if if_exists == "skip":
                    return
                if if_exists == "error":
                    raise WriteError(f"table {table!r} already exists")
                cur.execute(f"DROP TABLE IF EXISTS {self._qt(table)}")

            col_names = {c.name for c in columns}
            fragments: list[str] = []
            for c in columns:
                if not _SAFE_IDENT.match(c.name):
                    raise WriteError(f"ensure_table: invalid column name {c.name!r}")
                spec = normalize_db_type(c.type or "")
                rendered = render_canonical(spec, dialect="clickhouse")
                fragments.append(f"`{c.name}` {rendered}")

            # ClickHouse MergeTree requires a sorting key. Use the primary
            # key columns when given, else an empty tuple (allowed).
            order_by = "tuple()"
            if primary_key:
                for k in primary_key:
                    if not _SAFE_IDENT.match(k):
                        raise WriteError(f"ensure_table: invalid primary key column {k!r}")
                    if k not in col_names:
                        raise WriteError(f"ensure_table: primary key column {k!r} not in columns")
                order_by = "(" + ", ".join(f"`{k}`" for k in primary_key) + ")"

            ddl = (
                f"CREATE TABLE {self._qt(table)} ({', '.join(fragments)}) "
                f"ENGINE = MergeTree ORDER BY {order_by}"
            )
            cur.execute(ddl)
        finally:
            cur.close()

    # ---------- SqlExecutor (ADR-0035) -------------------------------------

    def execute_statement(self, statement: str) -> int:
        cur = self.connection.cursor()
        try:
            cur.execute(statement)
            n = cur.rowcount
            return int(n) if n is not None and n >= 0 else 0
        except Exception as exc:
            raise WriteError(f"clickhouse execute_statement failed: {exc}") from exc
        finally:
            cur.close()

    # ---------- BatchSource -------------------------------------------------

    def read(
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]:
        if query is None:
            raise ReadError("ClickHouseConnector.read requires a SQL query")
        cur = self.connection.cursor()
        try:
            cur.execute(query)
            columns = [d[0] for d in cur.description] if cur.description else []
            if not columns:
                return
            while True:
                rows = cur.fetchmany(chunk_size)
                if not rows:
                    break
                for row in rows:
                    yield Record(
                        data=dict(zip(columns, row, strict=False)),
                        metadata={"source": "clickhouse"},
                    )
        except Exception as exc:
            raise ReadError(f"clickhouse read failed: {exc}") from exc
        finally:
            cur.close()

    # ---------- BatchSink ---------------------------------------------------

    def write(
        self,
        records: Iterable[Record],
        *,
        mode: str = "append",
        key_columns: list[str] | None = None,
        table: str | None = None,
        pre_sql: str | None = None,
        batch_size: int = 1_000,
        **options: Any,
    ) -> int:
        if not table:
            raise WriteError("ClickHouseConnector.write requires 'table'")
        if mode == "upsert":
            raise WriteError(
                "ClickHouse has no row-level UPSERT. Use a ReplacingMergeTree "
                "table with mode='append', or DELETE+INSERT via pre_sql."
            )
        if mode not in ("append", "overwrite"):
            raise WriteError(
                f"unknown write mode: {mode!r} (use 'append' or 'overwrite'; "
                "ClickHouse does not support 'upsert')"
            )

        it = iter(records)
        first = next(it, None)
        cur = self.connection.cursor()
        try:
            if pre_sql:
                cur.execute(pre_sql)
            if first is None:
                return 0
            columns = list(first.data.keys())
            if mode == "overwrite":
                cur.execute(f"TRUNCATE TABLE {self._qt(table)}")
            return self._batch_insert(cur, table, columns, first, it, batch_size)
        except Exception as exc:
            raise WriteError(f"clickhouse write failed: {exc}") from exc
        finally:
            cur.close()

    # ---------- internal helpers -------------------------------------------

    def _batch_insert(
        self,
        cur: Any,
        table: str,
        columns: list[str],
        first: Record,
        rest: Iterator[Record],
        batch_size: int,
    ) -> int:
        """One multi-row ``INSERT ... VALUES`` per batch — ClickHouse is
        optimized for large bulk inserts, not row-at-a-time."""
        col_list = ", ".join(_q(c) for c in columns)
        row_placeholder = "(" + ", ".join(["%s"] * len(columns)) + ")"
        qt = self._qt(table)

        def flush(rows: list[Record]) -> int:
            if not rows:
                return 0
            values = ", ".join([row_placeholder] * len(rows))
            params: list[Any] = []
            for r in rows:
                params.extend(r.data.get(c) for c in columns)
            cur.execute(f"INSERT INTO {qt} ({col_list}) VALUES {values}", params)
            return len(rows)

        buf: list[Record] = [first]
        count = 0
        for r in rest:
            buf.append(r)
            if len(buf) >= batch_size:
                count += flush(buf)
                buf = []
        count += flush(buf)
        return count
