"""Snowflake connector — BatchSource + BatchSink (Phase AGE, ADR-0077).

Snowflake is a cloud data warehouse with a postgres-flavoured SQL dialect.
Built on the official ``snowflake-connector-python`` client. Optional
dependency::

    pip install 'etl-plugins[snowflake]'

Modes (``write``):

* ``append`` (default) — multi-row ``INSERT`` (``executemany``)
* ``overwrite`` — ``DELETE FROM <table>`` + ``INSERT``
* ``upsert`` — emulated via ``MERGE`` (``key_columns`` required)

Reads stream rows through the driver cursor's ``fetchmany`` so memory
stays bounded for large result sets.

The driver is imported **lazily** inside :meth:`connect` so the module
loads even when the extra isn't installed — the connector class still
registers, and the user gets a clear ``ConnectError`` the moment they
try to actually open a connection.

Identifier note: Snowflake folds *unquoted* identifiers to UPPERCASE.
This connector quotes every identifier it emits (double quotes), so the
case you give in a config / column list is preserved exactly, and the
catalog lookups (``INFORMATION_SCHEMA``) use that same case. Mixing this
connector's tables with externally-created unquoted (UPPERCASE) objects
requires passing the names in uppercase.
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


def _q(ident: str) -> str:
    """Quote an identifier with double quotes (Snowflake's standard)."""
    if not _SAFE_IDENT.match(ident):
        raise WriteError(f"unsafe identifier: {ident!r}")
    return f'"{ident}"'


def _qt(table: str) -> str:
    """Quote a possibly schema-qualified table name."""
    if not _SAFE_QUALIFIED_IDENT.match(table):
        raise WriteError(f"unsafe table name: {table!r}")
    return ".".join(f'"{p}"' for p in table.split("."))


@ConnectorRegistry.register("snowflake")
class SnowflakeConnector(BatchSource, BatchSink):
    """Snowflake batch source + sink."""

    def __init__(
        self,
        account: str = "",
        user: str = "",
        password: str = "",
        *,
        warehouse: str = "",
        database: str = "",
        schema: str = "PUBLIC",
        role: str = "",
        login_timeout: int = 10,
        **extra: Any,
    ) -> None:
        self.account = account
        self.user = user
        self.password = password
        self.warehouse = warehouse
        self.database = database
        self.schema = schema or "PUBLIC"
        self.role = role
        self.login_timeout = login_timeout
        self._extra: dict[str, Any] = extra
        self._conn: Any = None

    # ---------- lifecycle ---------------------------------------------------

    def connect(self) -> None:
        if self._conn is not None:
            return
        try:
            # Lazy import so the module loads without the driver.
            import snowflake.connector
        except ImportError as exc:  # pragma: no cover - import side effect
            raise ConnectError(
                "snowflake-connector-python not installed. Install with: "
                "pip install 'etl-plugins[snowflake]'"
            ) from exc
        # Only pass non-empty optional params so the driver applies its
        # own defaults / session settings where we don't override.
        params: dict[str, Any] = {
            "account": self.account,
            "user": self.user,
            "password": self.password,
            "login_timeout": self.login_timeout,
        }
        for key, val in (
            ("warehouse", self.warehouse),
            ("database", self.database),
            ("schema", self.schema),
            ("role", self.role),
        ):
            if val:
                params[key] = val
        params.update(self._extra)
        try:
            self._conn = snowflake.connector.connect(**params)
        except Exception as exc:  # snowflake.connector.errors.* is broad
            raise ConnectError(f"snowflake connect failed: {exc}") from exc

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
            raise ConnectError("SnowflakeConnector is not connected")
        return self._conn

    # ---------- SchemaInspector (ADR-0033) ---------------------------------

    def list_tables(self) -> list[str]:
        cur = self.connection.cursor()
        try:
            cur.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema <> 'INFORMATION_SCHEMA' "
                "ORDER BY table_schema, table_name"
            )
            rows = cur.fetchall()
        finally:
            cur.close()
        return [f"{schema}.{name}" for schema, name in rows]

    def list_columns(self, table: str) -> list[ColumnInfo]:
        """List columns from ``information_schema.columns``, folding
        length / precision / scale back into the type string so the
        canonical translator (Phase VV) keeps those specs across the
        dialect hop."""
        schema, sep, name = table.rpartition(".")
        if not sep:
            schema, name = self.schema, table
        cur = self.connection.cursor()
        try:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s "
                "ORDER BY ordinal_position",
                (schema, name),
            )
            rows = cur.fetchall()
        finally:
            cur.close()
        out: list[ColumnInfo] = []
        for col, dtype, char_len, prec, scale in rows:
            rendered = dtype
            dtype_low = str(dtype).lower()
            if char_len is not None and (
                "varchar" in dtype_low or "char" in dtype_low or dtype_low in ("text", "string")
            ):
                rendered = f"{dtype}({char_len})"
            elif dtype_low in ("number", "numeric", "decimal") and prec is not None:
                rendered = f"{dtype}({prec},{scale})" if scale is not None else f"{dtype}({prec})"
            out.append(ColumnInfo(name=col, type=rendered))
        return out

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

        schema, sep, name = table.rpartition(".")
        if not sep:
            schema, name = self.schema, table

        cur = self.connection.cursor()
        try:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s",
                (schema, name),
            )
            already = cur.fetchone() is not None
            if already:
                if if_exists == "skip":
                    return
                if if_exists == "error":
                    raise WriteError(f"table {table!r} already exists")
                cur.execute(f"DROP TABLE {_qt(table)}")

            col_names = {c.name for c in columns}
            fragments: list[str] = []
            for c in columns:
                if not _SAFE_IDENT.match(c.name):
                    raise WriteError(f"ensure_table: invalid column name {c.name!r}")
                spec = normalize_db_type(c.type or "")
                rendered = render_canonical(spec, dialect="snowflake")
                fragments.append(f'"{c.name}" {rendered}')
            if primary_key:
                for k in primary_key:
                    if not _SAFE_IDENT.match(k):
                        raise WriteError(f"ensure_table: invalid primary key column {k!r}")
                    if k not in col_names:
                        raise WriteError(f"ensure_table: primary key column {k!r} not in columns")
                pk_list = ", ".join(f'"{k}"' for k in primary_key)
                fragments.append(f"PRIMARY KEY ({pk_list})")
            ddl = f"CREATE TABLE {_qt(table)} ({', '.join(fragments)})"
            cur.execute(ddl)
        finally:
            cur.close()

    # ---------- SqlExecutor (ADR-0035) -------------------------------------

    def execute_statement(self, statement: str) -> int:
        cur = self.connection.cursor()
        try:
            cur.execute(statement)
            n = cur.rowcount
            return int(n) if n is not None else 0
        except Exception as exc:
            raise WriteError(f"snowflake execute_statement failed: {exc}") from exc
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
            raise ReadError("SnowflakeConnector.read requires a SQL query")
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
                        metadata={"source": "snowflake"},
                    )
        except Exception as exc:
            raise ReadError(f"snowflake read failed: {exc}") from exc
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
            raise WriteError("SnowflakeConnector.write requires 'table'")
        if mode == "upsert" and not key_columns:
            raise WriteError("mode='upsert' requires non-empty 'key_columns'")
        if mode not in ("append", "overwrite", "upsert"):
            raise WriteError(
                f"unknown write mode: {mode!r} (use 'append', 'overwrite', or 'upsert')"
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
                cur.execute(f"DELETE FROM {_qt(table)}")

            if mode in ("append", "overwrite"):
                count = self._batch_insert(cur, table, columns, first, it, batch_size)
            else:
                assert key_columns is not None
                count = self._merge_upsert(cur, table, columns, key_columns, first, it, batch_size)
            return count
        except Exception as exc:
            with contextlib.suppress(Exception):
                self.connection.rollback()
            raise WriteError(f"snowflake write failed: {exc}") from exc
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
        col_list = ", ".join(_q(c) for c in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        stmt = f"INSERT INTO {_qt(table)} ({col_list}) VALUES ({placeholders})"

        buf: list[tuple[Any, ...]] = [tuple(first.data.get(c) for c in columns)]
        count = 0
        for r in rest:
            buf.append(tuple(r.data.get(c) for c in columns))
            if len(buf) >= batch_size:
                cur.executemany(stmt, buf)
                count += len(buf)
                buf.clear()
        if buf:
            cur.executemany(stmt, buf)
            count += len(buf)
        return count

    def _merge_upsert(
        self,
        cur: Any,
        table: str,
        columns: list[str],
        key_columns: list[str],
        first: Record,
        rest: Iterator[Record],
        batch_size: int,
    ) -> int:
        """Emulate UPSERT via per-row MERGE. Snowflake supports MERGE with
        a constant-SELECT source on the right side."""
        non_key = [c for c in columns if c not in key_columns]
        col_list = ", ".join(_q(c) for c in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        src_cols = ", ".join(f"%s AS {_q(c)}" for c in columns)
        on_clause = " AND ".join(f"tgt.{_q(k)} = src.{_q(k)}" for k in key_columns)
        if non_key:
            set_clause = ", ".join(f"{_q(c)} = src.{_q(c)}" for c in non_key)
            merge = (
                f"MERGE INTO {_qt(table)} tgt "
                f"USING (SELECT {src_cols}) src "
                f"ON {on_clause} "
                f"WHEN MATCHED THEN UPDATE SET {set_clause} "
                f"WHEN NOT MATCHED THEN INSERT ({col_list}) VALUES ({placeholders})"
            )
        else:
            merge = (
                f"MERGE INTO {_qt(table)} tgt "
                f"USING (SELECT {src_cols}) src "
                f"ON {on_clause} "
                f"WHEN NOT MATCHED THEN INSERT ({col_list}) VALUES ({placeholders})"
            )

        def row_params(r: Record) -> tuple[Any, ...]:
            base = tuple(r.data.get(c) for c in columns)
            return base + base  # USING SELECT params, then INSERT VALUES

        count = 0
        cur.execute(merge, row_params(first))
        count += 1
        for r in rest:
            cur.execute(merge, row_params(r))
            count += 1
        return count
