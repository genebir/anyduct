"""BigQuery connector — BatchSource + BatchSink (Phase AGF, ADR-0078).

Google BigQuery is a serverless cloud data warehouse (GoogleSQL dialect).
Built on the official ``google-cloud-bigquery`` client's DB-API so it
mirrors the other SQL connectors (execute / fetchmany / executemany).
Optional dependency::

    pip install 'etl-plugins[bigquery]'

Auth: pass a service-account key as ``credentials_json`` (a dict or JSON
string, stored in the secret backend), or omit it to use Application
Default Credentials (ADC) from the environment.

Modes (``write``):

* ``append`` (default) — multi-row ``INSERT ... VALUES`` (one job/batch)
* ``overwrite`` — ``DELETE`` + ``INSERT``
* ``upsert`` — ``MERGE`` (``key_columns`` required)

Quoting uses backticks (GoogleSQL). Tables are referenced as
``dataset.table`` (or ``project.dataset.table``); a bare table name is
qualified with the connector's default ``dataset``.

Note on DML: BigQuery charges per-statement and enforces DML quotas, so
row-by-row writes are inefficient. This connector batches into multi-row
``INSERT``s (far fewer jobs than executemany), but high-volume loads
should use load jobs / the Storage Write API — a future optimization.

The driver is imported **lazily** inside :meth:`connect` so the module
loads even when the extra isn't installed.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Iterable, Iterator
from typing import Any

from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.inspect import ColumnInfo
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Up to project.dataset.table (3 dot-separated parts).
_SAFE_QUALIFIED_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}$")


def _q(ident: str) -> str:
    """Backtick-quote a single identifier (GoogleSQL)."""
    if not _SAFE_IDENT.match(ident):
        raise WriteError(f"unsafe identifier: {ident!r}")
    return f"`{ident}`"


@ConnectorRegistry.register("bigquery")
class BigQueryConnector(BatchSource, BatchSink):
    """BigQuery batch source + sink (GoogleSQL via the client DB-API)."""

    # Same-connection pushdown (ADR-0093 P2c): this dialect supports
    # ``INSERT INTO <table> <select>`` so source==sink pipelines can run
    # entirely inside the database (no data movement).
    supports_sql_pushdown = True

    def quote_table(self, table: str) -> str:
        """Identifier-quoted table path for in-database statements (same-
        connection pushdown, ADR-0094 f/u, 2026-06-12) — mirrors the write
        path's quoting so one config behaves identically on every data
        path (an unquoted INSERT INTO folds case and broke case-sensitive
        tables the moment pushdown engaged)."""
        return self._qt(table)

    def __init__(
        self,
        project: str = "",
        dataset: str = "",
        *,
        credentials_json: Any = None,
        location: str = "US",
        **extra: Any,
    ) -> None:
        self.project = project
        self.dataset = dataset
        self.credentials_json = credentials_json
        self.location = location
        self._extra: dict[str, Any] = extra
        self._conn: Any = None

    # ---------- table-name quoting -----------------------------------------

    def _qt(self, table: str) -> str:
        """Backtick-quote a (possibly) qualified table path. A bare name
        is prefixed with the connector's default ``dataset``."""
        if not _SAFE_QUALIFIED_IDENT.match(table):
            raise WriteError(f"unsafe table name: {table!r}")
        path = table if "." in table else f"{self.dataset}.{table}"
        return f"`{path}`"

    def _split(self, table: str) -> tuple[str | None, str, str]:
        """Return (project, dataset, name) for an INFORMATION_SCHEMA lookup."""
        parts = table.split(".")
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        if len(parts) == 2:
            return None, parts[0], parts[1]
        return None, self.dataset, table

    def _info_schema(self, project: str | None, dataset: str, view: str) -> str:
        """Backtick path to a dataset-scoped INFORMATION_SCHEMA view."""
        prefix = f"{project}.{dataset}" if project else dataset
        return f"`{prefix}`.INFORMATION_SCHEMA.{view}"

    # ---------- lifecycle ---------------------------------------------------

    def connect(self) -> None:
        if self._conn is not None:
            return
        try:
            from google.cloud import bigquery
            from google.cloud.bigquery import dbapi
        except ImportError as exc:  # pragma: no cover - import side effect
            raise ConnectError(
                "google-cloud-bigquery not installed. Install with: "
                "pip install 'etl-plugins[bigquery]'"
            ) from exc
        try:
            credentials = None
            if self.credentials_json:
                from google.oauth2 import service_account

                # Route through an Any alias so mypy is happy whether or not
                # google-auth's (partial) types are present in the env.
                sa: Any = service_account
                info = self.credentials_json
                if isinstance(info, str):
                    info = json.loads(info)
                credentials = sa.Credentials.from_service_account_info(info)
            client = bigquery.Client(
                project=self.project or None,
                credentials=credentials,
                location=self.location,
                **self._extra,
            )
            self._conn = dbapi.connect(client=client)
        except Exception as exc:  # google.* errors are broad
            raise ConnectError(f"bigquery connect failed: {exc}") from exc

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
            raise ConnectError("BigQueryConnector is not connected")
        return self._conn

    # ---------- SchemaInspector (ADR-0033) ---------------------------------

    def list_tables(self) -> list[str]:
        if not self.dataset:
            raise ReadError("BigQueryConnector.list_tables requires a 'dataset'")
        view = self._info_schema(self.project or None, self.dataset, "TABLES")
        cur = self.connection.cursor()
        try:
            cur.execute(f"SELECT table_name FROM {view} ORDER BY table_name")
            rows = cur.fetchall()
        finally:
            cur.close()
        return [f"{self.dataset}.{r[0]}" for r in rows]

    def list_columns(self, table: str) -> list[ColumnInfo]:
        project, dataset, name = self._split(table)
        view = self._info_schema(project, dataset, "COLUMNS")
        cur = self.connection.cursor()
        try:
            cur.execute(
                f"SELECT column_name, data_type FROM {view} "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (name,),
            )
            rows = cur.fetchall()
        finally:
            cur.close()
        return [ColumnInfo(name=col, type=str(dtype)) for col, dtype in rows]

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

        project, dataset, name = self._split(table)
        view = self._info_schema(project, dataset, "TABLES")
        cur = self.connection.cursor()
        try:
            cur.execute(f"SELECT 1 FROM {view} WHERE table_name = %s", (name,))
            already = cur.fetchone() is not None
            if already:
                if if_exists == "skip":
                    return
                if if_exists == "error":
                    raise WriteError(f"table {table!r} already exists")
                cur.execute(f"DROP TABLE {self._qt(table)}")

            col_names = {c.name for c in columns}
            fragments: list[str] = []
            for c in columns:
                if not _SAFE_IDENT.match(c.name):
                    raise WriteError(f"ensure_table: invalid column name {c.name!r}")
                spec = normalize_db_type(c.type or "")
                rendered = render_canonical(spec, dialect="bigquery")
                fragments.append(f"`{c.name}` {rendered}")
            if primary_key:
                for k in primary_key:
                    if not _SAFE_IDENT.match(k):
                        raise WriteError(f"ensure_table: invalid primary key column {k!r}")
                    if k not in col_names:
                        raise WriteError(f"ensure_table: primary key column {k!r} not in columns")
                pk_list = ", ".join(f"`{k}`" for k in primary_key)
                # BigQuery only supports unenforced primary keys.
                fragments.append(f"PRIMARY KEY ({pk_list}) NOT ENFORCED")
            ddl = f"CREATE TABLE {self._qt(table)} ({', '.join(fragments)})"
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
            raise WriteError(f"bigquery execute_statement failed: {exc}") from exc
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
            raise ReadError("BigQueryConnector.read requires a SQL query")
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
                        metadata={"source": "bigquery"},
                    )
        except Exception as exc:
            raise ReadError(f"bigquery read failed: {exc}") from exc
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
        batch_size: int = 500,
        **options: Any,
    ) -> int:
        if not table:
            raise WriteError("BigQueryConnector.write requires 'table'")
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
                # BigQuery has no TRUNCATE in DML; DELETE with always-true
                # predicate clears the table.
                cur.execute(f"DELETE FROM {self._qt(table)} WHERE TRUE")

            if mode in ("append", "overwrite"):
                count = self._batch_insert(cur, table, columns, first, it, batch_size)
            else:
                assert key_columns is not None
                count = self._merge_upsert(cur, table, columns, key_columns, first, it)
            return count
        except Exception as exc:
            raise WriteError(f"bigquery write failed: {exc}") from exc
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
        """One multi-row ``INSERT ... VALUES`` per batch — far fewer
        BigQuery jobs (and DML-quota hits) than executemany."""
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

    def _merge_upsert(
        self,
        cur: Any,
        table: str,
        columns: list[str],
        key_columns: list[str],
        first: Record,
        rest: Iterator[Record],
    ) -> int:
        """Per-row MERGE. BigQuery supports MERGE with a constant SELECT
        source (no FROM needed)."""
        non_key = [c for c in columns if c not in key_columns]
        col_list = ", ".join(_q(c) for c in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        src_cols = ", ".join(f"%s AS {_q(c)}" for c in columns)
        on_clause = " AND ".join(f"tgt.{_q(k)} = src.{_q(k)}" for k in key_columns)
        qt = self._qt(table)
        if non_key:
            set_clause = ", ".join(f"{_q(c)} = src.{_q(c)}" for c in non_key)
            merge = (
                f"MERGE INTO {qt} tgt "
                f"USING (SELECT {src_cols}) src "
                f"ON {on_clause} "
                f"WHEN MATCHED THEN UPDATE SET {set_clause} "
                f"WHEN NOT MATCHED THEN INSERT ({col_list}) VALUES ({placeholders})"
            )
        else:
            merge = (
                f"MERGE INTO {qt} tgt "
                f"USING (SELECT {src_cols}) src "
                f"ON {on_clause} "
                f"WHEN NOT MATCHED THEN INSERT ({col_list}) VALUES ({placeholders})"
            )

        def row_params(r: Record) -> list[Any]:
            base = [r.data.get(c) for c in columns]
            return base + base  # USING SELECT params, then INSERT VALUES

        count = 0
        cur.execute(merge, row_params(first))
        count += 1
        for r in rest:
            cur.execute(merge, row_params(r))
            count += 1
        return count
