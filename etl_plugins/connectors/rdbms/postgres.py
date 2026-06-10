"""PostgreSQL connector — BatchSource + BatchSink. SPEC.md §6.

Built on psycopg 3 (sync). Optional dependency::

    pip install 'etl-plugins[postgres]'

Modes (``write``):
  * ``append`` (default) — COPY-based bulk insert
  * ``overwrite`` — TRUNCATE + COPY
  * ``upsert`` — INSERT ... ON CONFLICT (``key_columns`` required)

Reads stream rows through a server-side cursor (``itersize=chunk_size``) so
memory usage stays bounded for arbitrarily large result sets.

Arrow fast path (ADR-0093 P2b): :meth:`read_arrow` / :meth:`write_arrow`
move data as Arrow RecordBatches over COPY csv — no per-cell Python object,
so bulk pipelines bypass the Record plane entirely (pyarrow required;
ships with the ``[duckdb]`` / ``[s3]`` extras).
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable, Iterator
from itertools import chain
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
from psycopg import sql

from etl_plugins.core.arrow import Partition
from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.inspect import ColumnInfo
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pyarrow as pa

# Identifier validation regexes for DDL string interpolation
# (psycopg.sql.Identifier handles parameterised values; identifiers
# don't accept placeholders, so we whitelist before substitution).
_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_QUALIFIED_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


@ConnectorRegistry.register("postgres")
class PostgresConnector(BatchSource, BatchSink):
    """PostgreSQL batch source + sink."""

    # Same-connection pushdown (ADR-0093 P2c): this dialect supports
    # ``INSERT INTO <table> <select>`` so source==sink pipelines can run
    # entirely inside the database (no data movement).
    supports_sql_pushdown = True

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
        """Postgres column metadata.

        Phase VV (ADR-0066): when a column declares precision/scale or a
        character_maximum_length, fold them back into the returned type
        string (``NUMERIC(10,2)`` rather than bare ``NUMERIC``,
        ``VARCHAR(64)`` rather than ``character varying``). The
        translator in :mod:`etl_plugins.core.type_mapping` then keeps
        those specs across the dialect hop.
        """
        schema, sep, name = table.rpartition(".")
        if not sep:
            schema, name = "public", table
        with self.connection.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s "
                "ORDER BY ordinal_position",
                (schema, name),
            )
            out: list[ColumnInfo] = []
            for col, dtype, char_len, prec, scale in cur.fetchall():
                rendered = dtype
                if char_len is not None and "char" in dtype:
                    rendered = f"{dtype}({char_len})"
                elif dtype == "numeric" and prec is not None:
                    if scale is not None:
                        rendered = f"{dtype}({prec},{scale})"
                    else:
                        rendered = f"{dtype}({prec})"
                out.append(ColumnInfo(name=col, type=rendered))
            return out

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

        The vendor type string of each column is normalised through
        :mod:`etl_plugins.core.type_mapping` and rendered back in
        postgres's vocabulary — so a sqlite ``INTEGER`` becomes ``INTEGER``,
        a mysql ``DATETIME`` becomes ``TIMESTAMPTZ``, etc. ``schema.name``
        is honoured; a bare name lands in ``public``.

        ``primary_key`` (Phase AAC, ADR-0072) — when supplied, emits a
        ``PRIMARY KEY (...)`` table constraint. Required for upsert
        targets so ``INSERT ... ON CONFLICT (key_columns)`` can attach.
        """
        from etl_plugins.core.type_mapping import normalize_db_type, render_canonical

        if not _SAFE_QUALIFIED_IDENT.match(table):
            raise WriteError(f"invalid table name for ensure_table: {table!r}")
        if not columns:
            raise WriteError(f"ensure_table({table!r}) requires a non-empty column list")

        schema, sep, name = table.rpartition(".")
        if not sep:
            schema, name = "public", table

        # Phase AAR (2026-06-01) — cross-DB migrations into Postgres
        # often carry a schema-qualified name from the source
        # (Vertica's ``BDA_BI_DB.TB_XYZ``, MSSQL's ``dbo.X``, etc.).
        # If the target schema doesn't exist yet the CREATE TABLE
        # fails with ``schema "BDA_BI_DB" does not exist``, leaves
        # the transaction aborted, and the subsequent COPY trips
        # over "current transaction is aborted". CREATE SCHEMA IF
        # NOT EXISTS up front sidesteps the whole class — Postgres
        # treats unknown schemas as a real error, not a typo, so
        # making one is exactly what the operator wanted when they
        # picked ``auto_create_table=true``.
        if schema != "public" and _SAFE_IDENT.match(schema):
            with self.connection.cursor() as cur:
                cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

        with self.connection.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s",
                (schema, name),
            )
            already_exists = cur.fetchone() is not None
        if already_exists:
            if if_exists == "skip":
                return
            if if_exists == "error":
                raise WriteError(f"table {table!r} already exists")
            if if_exists == "drop":
                with self.connection.cursor() as cur:
                    cur.execute(f'DROP TABLE "{schema}"."{name}"')
        elif if_exists not in {"skip", "drop", "error"}:
            raise WriteError(
                f"ensure_table: unknown if_exists={if_exists!r} (use 'skip', 'drop', or 'error')"
            )

        col_names = {c.name for c in columns}
        col_fragments: list[str] = []
        for c in columns:
            if not _SAFE_IDENT.match(c.name):
                raise WriteError(
                    f"ensure_table: invalid column name {c.name!r} "
                    f"(must match {_SAFE_IDENT.pattern})"
                )
            spec = normalize_db_type(c.type or "")
            pg_type = render_canonical(spec, dialect="postgres")
            col_fragments.append(f'"{c.name}" {pg_type}')
        if primary_key:
            for k in primary_key:
                if not _SAFE_IDENT.match(k):
                    raise WriteError(f"ensure_table: invalid primary key column {k!r}")
                if k not in col_names:
                    raise WriteError(f"ensure_table: primary key column {k!r} not in columns")
            pk_list = ", ".join(f'"{k}"' for k in primary_key)
            col_fragments.append(f"PRIMARY KEY ({pk_list})")
        ddl = f'CREATE TABLE "{schema}"."{name}" ({", ".join(col_fragments)})'
        with self.connection.cursor() as cur:
            cur.execute(ddl)

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
        pre_sql: str | None = None,
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
        # ``pre_sql`` (ADR-0035 atomic variant) runs as the first statement in
        # the write transaction so a DELETE + the COPY commit together — atomic
        # delete-then-insert. It runs even on empty input (clears the partition).
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

    # ---------- Arrow fast path (ADR-0093 P2b) ------------------------------

    def read_arrow(
        self,
        *,
        query: str | None = None,
        partition: Partition | None = None,
        **options: Any,
    ) -> Iterator[pa.RecordBatch]:
        """Bulk read as Arrow RecordBatches via ``COPY (query) TO STDOUT csv``.

        The CSV bytes stream straight into pyarrow's vectorized CSV reader —
        no per-cell Python object. Column types come from a ``LIMIT 0`` probe
        of the query (OID → Arrow mapping below); unmapped types (e.g.
        ``numeric``) fall back to pyarrow's inference. ``partition`` narrows
        the read to one half-open ``(lower, upper]`` slice for split reads.
        """
        if query is None:
            raise ReadError("PostgresConnector.read_arrow requires a SQL query")
        if self._conn is None:
            raise ConnectError("PostgresConnector is not connected")
        import pyarrow as pa
        import pyarrow.csv as pacsv

        wrapped = sql.SQL("({})").format(sql.SQL(query))
        if partition is not None:
            col = sql.Identifier(partition.column)
            clauses: list[sql.Composable] = []
            if partition.lower is not None:
                clauses.append(sql.SQL("{} > {}").format(col, sql.Literal(partition.lower)))
            if partition.upper is not None:
                clauses.append(sql.SQL("{} <= {}").format(col, sql.Literal(partition.upper)))
            if clauses:
                wrapped = sql.SQL("(SELECT * FROM {} AS __p WHERE {})").format(
                    wrapped, sql.SQL(" AND ").join(clauses)
                )

        try:
            # Schema probe: drive pyarrow's column types from the real
            # result OIDs so '123' stays text when the column is text.
            with self._conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT * FROM {} AS __probe LIMIT 0").format(wrapped))
                description = cur.description or []
            column_types = {
                d.name: t for d in description if (t := _arrow_type_for_oid(d.type_code))
            }
            convert = pacsv.ConvertOptions(
                column_types=column_types,
                # Postgres CSV: NULL = unquoted empty, '' = quoted empty,
                # and booleans render as t/f.
                strings_can_be_null=True,
                quoted_strings_can_be_null=False,
                true_values=["t", "true"],
                false_values=["f", "false"],
            )
            # No readahead thread: pyarrow would read the psycopg COPY
            # stream from a worker thread, and psycopg connections are not
            # thread-safe (observed as a silent deadlock).
            read_opts = pacsv.ReadOptions(use_threads=False)
            copy_stmt = sql.SQL("COPY {} TO STDOUT (FORMAT csv, HEADER true)").format(wrapped)
            with self._conn.cursor() as cur, cur.copy(copy_stmt) as copy:
                reader = pacsv.open_csv(
                    _CopyByteStream(copy), read_options=read_opts, convert_options=convert
                )
                yield from reader
        except psycopg.Error as exc:
            raise ReadError(f"postgres read_arrow failed: {exc}") from exc
        except pa.ArrowInvalid as exc:
            raise ReadError(f"postgres read_arrow: CSV→Arrow conversion failed: {exc}") from exc

    def write_arrow(
        self,
        batches: Iterable[pa.RecordBatch],
        *,
        table: str | None = None,
        mode: str = "append",
        key_columns: list[str] | None = None,
        pre_sql: str | None = None,
        **options: Any,
    ) -> int:
        """Bulk write Arrow RecordBatches via ``COPY ... FROM STDIN csv``.

        Each batch is serialized by pyarrow's vectorized CSV writer and
        streamed into COPY — no per-row Python loop. Supports ``append`` /
        ``overwrite`` (upsert needs per-row conflict handling — use the
        Record path). NULL→unquoted, ''→quoted (``all_valid`` quoting), so
        the empty-string/NULL distinction survives.
        """
        if self._conn is None:
            raise ConnectError("PostgresConnector is not connected")
        if not table:
            raise WriteError("PostgresConnector.write_arrow requires 'table'")
        if mode not in ("append", "overwrite"):
            raise WriteError(
                f"write_arrow supports 'append'/'overwrite', got {mode!r} "
                "(upsert routes through the Record path)"
            )
        import pyarrow.csv as pacsv

        it = iter(batches)
        first = next(it, None)
        if first is None and not pre_sql:
            return 0
        try:
            if pre_sql:
                with self._conn.cursor() as cur:
                    cur.execute(pre_sql)
            if first is None:
                self._conn.commit()
                return 0
            columns = list(first.schema.names)
            if mode == "overwrite":
                with self._conn.cursor() as cur:
                    cur.execute(sql.SQL("TRUNCATE TABLE {}").format(_table_ident(table)))
            copy_stmt = sql.SQL("COPY {table} ({cols}) FROM STDIN (FORMAT csv)").format(
                table=_table_ident(table),
                cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
            )
            write_opts = pacsv.WriteOptions(include_header=False, quoting_style="all_valid")
            count = 0
            with self._conn.cursor() as cur, cur.copy(copy_stmt) as copy:
                for batch in chain([first], it):
                    if list(batch.schema.names) != columns:
                        # COPY is positional — reorder (or fail clearly when
                        # a batch is missing columns).
                        try:
                            batch = batch.select(columns)
                        except KeyError as exc:
                            raise WriteError(
                                f"write_arrow: batch schema drifted from first batch: {exc}"
                            ) from exc
                    buf = io.BytesIO()
                    pacsv.write_csv(batch, buf, write_options=write_opts)
                    copy.write(buf.getvalue())
                    count += batch.num_rows
            self._conn.commit()
            return count
        except psycopg.Error as exc:
            self._conn.rollback()
            raise WriteError(f"postgres write_arrow failed: {exc}") from exc

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


class _CopyByteStream:
    """Minimal file-like over a psycopg COPY TO stream for pyarrow.

    Arrow's PythonFile treats a short read as EOF, while ``Copy.read()``
    returns driver-sized chunks — so buffer until the requested size is
    available (or the stream truly ends).
    """

    def __init__(self, copy: Any) -> None:
        self._copy = copy
        self._buf = bytearray()
        self._eof = False

    def read(self, size: int = -1) -> bytes:
        while not self._eof and (size < 0 or len(self._buf) < size):
            chunk = self._copy.read()
            if not chunk:
                self._eof = True
                break
            self._buf.extend(chunk)
        if size < 0 or size >= len(self._buf):
            out = bytes(self._buf)
            self._buf.clear()
            return out
        out = bytes(self._buf[:size])
        del self._buf[:size]
        return out

    def readable(self) -> bool:
        return True

    @property
    def closed(self) -> bool:
        # pyarrow checks the ATTRIBUTE — a plain method here reads as a
        # truthy bound method, i.e. "closed", and open_csv refuses the file.
        return False

    def close(self) -> None:  # pragma: no cover - stream owned by the cursor
        return None


def _arrow_type_for_oid(oid: int) -> pa.DataType | None:
    """Map common Postgres type OIDs to Arrow types for CSV conversion.

    ``None`` = let pyarrow infer (e.g. ``numeric`` — precision-bearing
    decimals don't round-trip CSV faithfully; inference yields float64,
    documented loss for the fast path).
    """
    import pyarrow as pa

    mapping: dict[int, pa.DataType] = {
        16: pa.bool_(),  # bool
        20: pa.int64(),  # int8
        21: pa.int16(),  # int2
        23: pa.int32(),  # int4
        25: pa.string(),  # text
        700: pa.float32(),  # float4
        701: pa.float64(),  # float8
        1042: pa.string(),  # bpchar
        1043: pa.string(),  # varchar
        1082: pa.date32(),  # date
        1114: pa.timestamp("us"),  # timestamp
        1184: pa.timestamp("us", tz="UTC"),  # timestamptz
        2950: pa.string(),  # uuid
        114: pa.string(),  # json
        3802: pa.string(),  # jsonb
    }
    return mapping.get(oid)


def _table_ident(table: str) -> sql.Composed:
    """Quote a possibly schema-qualified table name (e.g. 'public.orders')."""
    parts = table.split(".")
    return sql.SQL(".").join(sql.Identifier(p) for p in parts)
