"""Vertica connector — BatchSource + BatchSink (Phase AAQ, 2026-05-29).

Vertica is a column-oriented analytical database with a postgres-flavoured
SQL dialect. Built on the pure-Python ``vertica-python`` client. Optional
dependency::

    pip install 'etl-plugins[vertica]'

Modes (``write``):

* ``append`` (default) — multi-row ``INSERT`` (``executemany``)
* ``overwrite`` — ``DELETE FROM <table>`` + ``INSERT``
* ``upsert`` — emulated via ``MERGE`` (``key_columns`` required)

Reads stream rows through ``vertica-python``'s server-side cursor so
memory stays bounded for large result sets.

The driver is imported **lazily** inside :meth:`connect` so the module
loads even when the extra isn't installed — the connector class still
registers via entry-points, and the user gets a clear ``ConnectError``
the moment they try to actually open a connection.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable, Iterator
from itertools import chain
from typing import TYPE_CHECKING, Any

from etl_plugins.core.arrow import DEFAULT_BATCH_ROWS, Partition
from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.inspect import ColumnInfo
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pyarrow as pa

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_QUALIFIED_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _q(ident: str) -> str:
    """Quote an identifier with double quotes (vertica's standard)."""
    if not _SAFE_IDENT.match(ident):
        raise WriteError(f"unsafe identifier: {ident!r}")
    return f'"{ident}"'


def _qt(table: str) -> str:
    """Quote a possibly schema-qualified table name."""
    if not _SAFE_QUALIFIED_IDENT.match(table):
        raise WriteError(f"unsafe table name: {table!r}")
    return ".".join(f'"{p}"' for p in table.split("."))


def _coerce_ssl(value: Any) -> Any:
    """Normalise an ``ssl`` config value into what vertica-python
    accepts (bool or ssl.SSLContext). YAML / web-form round-trips
    can hand us a string literal — turn ``"true"`` / ``"false"`` /
    ``""`` into a real bool; pass anything else through unchanged so
    ``SSLContext`` instances and explicit booleans aren't disturbed."""
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off", ""):
            return False
    return value


@ConnectorRegistry.register("vertica")
class VerticaConnector(BatchSource, BatchSink):
    """Vertica batch source + sink."""

    # Same-connection pushdown (ADR-0093 P2c): this dialect supports
    # ``INSERT INTO <table> <select>`` so source==sink pipelines can run
    # entirely inside the database (no data movement).
    supports_sql_pushdown = True

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5433,
        database: str = "",
        user: str = "",
        password: str = "",
        *,
        connection_timeout: int = 10,
        ssl: bool | str | Any = False,
        **extra: Any,
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.connection_timeout = connection_timeout
        # Phase AAQ post-mortem 3 (2026-05-29) — connection config
        # round-tripped through YAML / web form can deliver ``ssl`` as a
        # string ("true" / "false") instead of an actual bool, and
        # vertica-python's driver rejects that with a confusing
        # ``"ssl should be a bool or ssl.SSLContext"``. Coerce string
        # literals here so the connector is robust across input paths;
        # SSLContext instances pass through untouched.
        self.ssl = _coerce_ssl(ssl)
        self._extra: dict[str, Any] = extra
        self._conn: Any = None

    # ---------- lifecycle ---------------------------------------------------

    def connect(self) -> None:
        if self._conn is not None:
            return
        try:
            # Lazy import so the module loads without the driver.
            import vertica_python
        except ImportError as exc:  # pragma: no cover - import side effect
            raise ConnectError(
                "vertica-python not installed. Install with: pip install 'etl-plugins[vertica]'"
            ) from exc
        try:
            self._conn = vertica_python.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                connection_timeout=self.connection_timeout,
                ssl=self.ssl,
                **self._extra,
            )
        except Exception as exc:  # vertica_python.errors.* is broad
            raise ConnectError(f"vertica connect failed: {exc}") from exc

    def close(self) -> None:
        if self._conn is not None:
            # ``vertica_python.errors.*`` is broad and the connection is
            # already going away — best-effort close + nullify.
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
            raise ConnectError("VerticaConnector is not connected")
        return self._conn

    # ---------- SchemaInspector (ADR-0033) ---------------------------------

    def list_tables(self) -> list[str]:
        cur = self.connection.cursor()
        try:
            cur.execute(
                "SELECT table_schema, table_name FROM v_catalog.tables "
                "WHERE NOT is_system_table "
                "ORDER BY table_schema, table_name"
            )
            rows = cur.fetchall()
        finally:
            cur.close()
        return [f"{schema}.{name}" for schema, name in rows]

    def list_columns(self, table: str) -> list[ColumnInfo]:
        """List columns from ``v_catalog.columns``, folding length /
        precision / scale back into the type string so the canonical
        translator (Phase VV) keeps those specs across the dialect
        hop."""
        schema, sep, name = table.rpartition(".")
        if not sep:
            schema, name = "public", table
        cur = self.connection.cursor()
        try:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "numeric_precision, numeric_scale "
                "FROM v_catalog.columns "
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
            if char_len is not None and ("varchar" in dtype_low or "char" in dtype_low):
                rendered = f"{dtype}({char_len})"
            elif dtype_low.startswith("numeric") and prec is not None:
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
            schema, name = "public", table

        cur = self.connection.cursor()
        try:
            cur.execute(
                "SELECT 1 FROM v_catalog.tables WHERE table_schema = %s AND table_name = %s",
                (schema, name),
            )
            already = cur.fetchone() is not None
            if already:
                if if_exists == "skip":
                    return
                if if_exists == "error":
                    raise WriteError(f"table {table!r} already exists")
                # drop
                cur.execute(f"DROP TABLE {_qt(table)}")
                self.connection.commit()

            col_names = {c.name for c in columns}
            fragments: list[str] = []
            for c in columns:
                if not _SAFE_IDENT.match(c.name):
                    raise WriteError(f"ensure_table: invalid column name {c.name!r}")
                spec = normalize_db_type(c.type or "")
                rendered = render_canonical(spec, dialect="vertica")
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
            self.connection.commit()
        finally:
            cur.close()

    # ---------- SqlExecutor (ADR-0035) -------------------------------------

    def execute_statement(self, statement: str) -> int:
        cur = self.connection.cursor()
        try:
            cur.execute(statement)
            n = cur.rowcount
            self.connection.commit()
            return int(n)
        except Exception as exc:
            self.connection.rollback()
            raise WriteError(f"vertica execute_statement failed: {exc}") from exc
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
            raise ReadError("VerticaConnector.read requires a SQL query")
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
                        metadata={"source": "vertica"},
                    )
        except Exception as exc:
            raise ReadError(f"vertica read failed: {exc}") from exc
        finally:
            cur.close()

    # ---------- Arrow fast path (ADR-0093, 2026-06-12) ----------------------

    def read_arrow(
        self,
        *,
        query: str | None = None,
        partition: Partition | None = None,
        **options: Any,
    ) -> Iterator[pa.RecordBatch]:
        """Bulk read as Arrow RecordBatches.

        Vertica has no client-streamable COPY-to-stdout equivalent in
        ``vertica-python``, so this follows the mysql shape: stream tuple
        rows through the server-side cursor and assemble columnar batches
        directly — the win is skipping the per-row ``Record``/pydantic
        layer. Column types pin from the cursor description's Vertica type
        codes where unambiguous; NUMERIC pins to the *declared*
        precision/scale (first-chunk inference under-sizes the type the
        moment a later chunk carries more integer digits — the mysql
        integration lesson). Ambiguous codes infer from the first chunk
        and then freeze. ``partition`` narrows the read to one half-open
        ``(lower, upper]`` slice via a parameterised predicate.
        """
        if query is None:
            raise ReadError("VerticaConnector.read_arrow requires a SQL query")
        import pyarrow as pa

        sql_text = query
        params: tuple[Any, ...] = ()
        if partition is not None:
            clauses: list[str] = []
            values: list[Any] = []
            if partition.lower is not None:
                clauses.append(f"{_q(partition.column)} > %s")
                values.append(partition.lower)
            if partition.upper is not None:
                clauses.append(f"{_q(partition.column)} <= %s")
                values.append(partition.upper)
            if clauses:
                sql_text = f"SELECT * FROM ({query}) AS __p WHERE {' AND '.join(clauses)}"
                params = tuple(values)

        chunk_rows = int(options.get("chunk_size", DEFAULT_BATCH_ROWS))
        cur = self.connection.cursor()
        try:
            cur.execute(sql_text, params or None)
            description = cur.description or []
            names = [d[0] for d in description]
            if not names:
                return
            # Per-column Arrow type: pinned by Vertica type code where
            # deterministic, None = infer from the first chunk then lock.
            locked: list[pa.DataType | None] = [_arrow_type_for_vertica(d) for d in description]
            while True:
                rows = cur.fetchmany(chunk_rows)
                if not rows:
                    return
                columns = list(zip(*rows, strict=False))
                arrays = []
                for i in range(len(names)):
                    arr = pa.array(list(columns[i]), type=locked[i])
                    if locked[i] is None and not pa.types.is_null(arr.type):
                        locked[i] = arr.type
                    arrays.append(arr)
                yield pa.RecordBatch.from_arrays(arrays, names=names)
        except (pa.ArrowInvalid, pa.ArrowTypeError) as exc:
            raise ReadError(f"vertica read_arrow: Arrow conversion failed: {exc}") from exc
        except ReadError:
            raise
        except Exception as exc:
            raise ReadError(f"vertica read_arrow failed: {exc}") from exc
        finally:
            cur.close()

    def write_arrow(
        self,
        batches: Iterable[pa.RecordBatch],
        *,
        table: str | None = None,
        mode: str = "append",
        key_columns: list[str] | None = None,
        pre_sql: str | None = None,
        batch_size: int = 1_000,
        **options: Any,
    ) -> int:
        """Bulk write Arrow RecordBatches via multi-row ``executemany``.

        Same transactional semantics as ``write`` (``pre_sql`` runs first
        inside the transaction; ``overwrite`` issues ``DELETE FROM``).
        Supports ``append`` / ``overwrite`` — upsert routes through the
        Record path (MERGE needs per-row binds anyway).
        """
        if not table:
            raise WriteError("VerticaConnector.write_arrow requires 'table'")
        if mode not in ("append", "overwrite"):
            raise WriteError(
                f"write_arrow supports 'append'/'overwrite', got {mode!r} "
                "(upsert routes through the Record path)"
            )

        it = iter(batches)
        first = next(it, None)
        if first is None and not pre_sql:
            return 0
        cur = self.connection.cursor()
        try:
            if pre_sql:
                cur.execute(pre_sql)
            if first is None:
                self.connection.commit()
                return 0
            columns = list(first.schema.names)
            if mode == "overwrite":
                cur.execute(f"DELETE FROM {_qt(table)}")
            col_list = ", ".join(_q(c) for c in columns)
            placeholders = ", ".join(["%s"] * len(columns))
            stmt = f"INSERT INTO {_qt(table)} ({col_list}) VALUES ({placeholders})"
            count = 0
            for batch in chain([first], it):
                if list(batch.schema.names) != columns:
                    try:
                        batch = batch.select(columns)
                    except KeyError as exc:
                        raise WriteError(
                            f"write_arrow: batch schema drifted from first batch: {exc}"
                        ) from exc
                rows = batch.to_pylist()
                for start in range(0, len(rows), batch_size):
                    slice_ = rows[start : start + batch_size]
                    cur.executemany(stmt, [tuple(r.get(c) for c in columns) for r in slice_])
                    count += len(slice_)
            self.connection.commit()
            return count
        except WriteError:
            self.connection.rollback()
            raise
        except Exception as exc:
            self.connection.rollback()
            raise WriteError(f"vertica write_arrow failed: {exc}") from exc
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
            raise WriteError("VerticaConnector.write requires 'table'")
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
                self.connection.commit()
                return 0
            columns = list(first.data.keys())
            if mode == "overwrite":
                cur.execute(f"DELETE FROM {_qt(table)}")

            if mode in ("append", "overwrite"):
                count = self._batch_insert(cur, table, columns, first, it, batch_size)
            else:
                assert key_columns is not None
                count = self._merge_upsert(cur, table, columns, key_columns, first, it, batch_size)
            self.connection.commit()
            return count
        except Exception as exc:
            self.connection.rollback()
            raise WriteError(f"vertica write failed: {exc}") from exc
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
        """Emulate UPSERT via per-row MERGE. Vertica supports MERGE with
        a values constructor on the right side."""
        non_key = [c for c in columns if c not in key_columns]
        col_list = ", ".join(_q(c) for c in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        # Vertica MERGE expects a USING source — wrap the VALUES in a SELECT.
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
            # Each row binds N values for the USING select, then N more
            # for the INSERT VALUES — total 2N.
            params_per_row = 2 * len(columns)
        else:
            # All columns are the key — no update branch.
            merge = (
                f"MERGE INTO {_qt(table)} tgt "
                f"USING (SELECT {src_cols}) src "
                f"ON {on_clause} "
                f"WHEN NOT MATCHED THEN INSERT ({col_list}) VALUES ({placeholders})"
            )
            params_per_row = 2 * len(columns)

        def row_params(r: Record) -> tuple[Any, ...]:
            base = tuple(r.data.get(c) for c in columns)
            return base + base  # USING SELECT params, then INSERT VALUES

        count = 0
        cur.execute(merge, row_params(first))
        count += 1
        for r in rest:
            cur.execute(merge, row_params(r))
            count += 1
        # ``params_per_row`` retained for symmetry with sister connectors
        # — not used at runtime because we bind per call.
        _ = params_per_row
        return count


def _arrow_type_for_vertica(description: Any) -> Any:
    """Map an unambiguous Vertica type code to an Arrow type.

    ``description`` is one ``vertica-python`` cursor-description entry
    (a ``Column`` namedtuple, tuple-indexable per DBAPI: ``(name,
    type_code, display_size, internal_size, precision, scale,
    null_ok)``). NUMERIC pins to the *declared* precision/scale — the
    mysql DECIMAL lesson: first-chunk inference under-sizes the type as
    soon as a later chunk carries more integer digits. Returns ``None``
    for codes whose Python value shape the driver decides at conversion
    time (TIMESTAMPTZ is tz-aware so pyarrow needs to see a value to pick
    the tz; INTERVAL/TIME/UUID arrive as exotic objects) — those infer
    from the first chunk and are then locked by the caller.
    """
    import pyarrow as pa
    from vertica_python.datatypes import VerticaType

    type_code = description[1]
    if type_code == VerticaType.BOOL:
        return pa.bool_()
    if type_code == VerticaType.INT8:
        return pa.int64()
    if type_code == VerticaType.FLOAT8:
        return pa.float64()
    if type_code in (VerticaType.CHAR, VerticaType.VARCHAR, VerticaType.LONGVARCHAR):
        return pa.string()
    if type_code == VerticaType.DATE:
        return pa.date32()
    if type_code == VerticaType.TIMESTAMP:
        return pa.timestamp("us")
    if type_code in (VerticaType.BINARY, VerticaType.VARBINARY, VerticaType.LONGVARBINARY):
        return pa.binary()
    if type_code == VerticaType.NUMERIC:
        precision = description[4]
        scale = description[5]
        if isinstance(precision, int) and isinstance(scale, int) and 1 <= precision <= 38:
            return pa.decimal128(precision, scale)
        if isinstance(precision, int) and isinstance(scale, int) and precision <= 76:
            return pa.decimal256(precision, scale)
        return None
    return None
