"""Cassandra connector — BatchSource + BatchSink (Phase AGK, ADR-0082).

Apache Cassandra is a wide-column store with a tabular query language
(CQL). Built on the official ``cassandra-driver``. Optional dependency::

    pip install 'etl-plugins[cassandra]'

Because CQL is tabular (``CREATE TABLE`` + ``PRIMARY KEY``), this
connector implements ``SchemaInspector`` / ``SchemaWriter`` and is a
valid cross-DB migration target — unlike DynamoDB.

Modes (``write``):

* ``append`` / ``upsert`` — both ``INSERT`` (in Cassandra every INSERT
  replaces by primary key, so the two are equivalent).
* ``overwrite`` — ``TRUNCATE`` + ``INSERT``.

Cassandra has no JOINs and discourages ``ALLOW FILTERING``; the source
reads a straight ``SELECT``. The driver uses a ``Session`` (not a DB-API
cursor), so ``session.execute`` is called directly. Imported lazily.
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
    """Double-quote a CQL identifier."""
    if not _SAFE_IDENT.match(ident):
        raise WriteError(f"unsafe identifier: {ident!r}")
    return f'"{ident}"'


def _qt(table: str) -> str:
    """Quote a possibly keyspace-qualified table name."""
    if not _SAFE_QUALIFIED_IDENT.match(table):
        raise WriteError(f"unsafe table name: {table!r}")
    return ".".join(f'"{p}"' for p in table.split("."))


@ConnectorRegistry.register("cassandra")
class CassandraConnector(BatchSource, BatchSink):
    """Cassandra batch source + sink (CQL wide-column store)."""

    def __init__(
        self,
        contact_points: Any = "localhost",
        port: int = 9042,
        keyspace: str = "",
        *,
        username: str = "",
        password: str = "",
        **extra: Any,
    ) -> None:
        # contact_points may arrive as a comma-separated string (web form)
        # or a list (YAML); normalise to a list.
        if isinstance(contact_points, str):
            self.contact_points = [p.strip() for p in contact_points.split(",") if p.strip()]
        else:
            self.contact_points = list(contact_points)
        self.port = port
        self.keyspace = keyspace
        self.username = username
        self.password = password
        self._extra: dict[str, Any] = extra
        self._cluster: Any = None
        self._session: Any = None

    # ---------- lifecycle ---------------------------------------------------

    def connect(self) -> None:
        if self._session is not None:
            return
        try:
            from cassandra.auth import PlainTextAuthProvider
            from cassandra.cluster import Cluster
        except ImportError as exc:  # pragma: no cover - import side effect
            raise ConnectError(
                "cassandra-driver not installed. Install with: pip install 'etl-plugins[cassandra]'"
            ) from exc
        try:
            auth = None
            if self.username:
                auth = PlainTextAuthProvider(username=self.username, password=self.password)
            self._cluster = Cluster(
                contact_points=self.contact_points,
                port=self.port,
                auth_provider=auth,
                **self._extra,
            )
            self._session = self._cluster.connect(self.keyspace or None)
        except Exception as exc:  # cassandra.* errors are broad
            raise ConnectError(f"cassandra connect failed: {exc}") from exc

    def close(self) -> None:
        if self._cluster is not None:
            with contextlib.suppress(Exception):
                self._cluster.shutdown()
        self._cluster = None
        self._session = None

    def health_check(self) -> bool:
        if self._session is None:
            return False
        try:
            self._session.execute("SELECT release_version FROM system.local")
            return True
        except Exception:
            return False

    @property
    def session(self) -> Any:
        if self._session is None:
            raise ConnectError("CassandraConnector is not connected")
        return self._session

    def _ks_table(self, table: str) -> tuple[str, str]:
        ks, sep, name = table.rpartition(".")
        if not sep:
            ks, name = self.keyspace, table
        return ks, name

    # ---------- SchemaInspector (ADR-0033) ---------------------------------

    def list_tables(self) -> list[str]:
        rows = self.session.execute("SELECT keyspace_name, table_name FROM system_schema.tables")
        out: list[str] = []
        skip = {"system", "system_schema", "system_auth", "system_distributed", "system_traces"}
        for r in rows:
            ks = r.keyspace_name
            if ks in skip:
                continue
            out.append(f"{ks}.{r.table_name}")
        return sorted(out)

    def list_columns(self, table: str) -> list[ColumnInfo]:
        ks, name = self._ks_table(table)
        rows = self.session.execute(
            "SELECT column_name, type FROM system_schema.columns "
            "WHERE keyspace_name = %s AND table_name = %s",
            (ks, name),
        )
        return [ColumnInfo(name=r.column_name, type=str(r.type)) for r in rows]

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

        ks, name = self._ks_table(table)
        existing = self.session.execute(
            "SELECT table_name FROM system_schema.tables "
            "WHERE keyspace_name = %s AND table_name = %s",
            (ks, name),
        )
        already = existing.one() is not None if hasattr(existing, "one") else bool(list(existing))
        if already:
            if if_exists == "skip":
                return
            if if_exists == "error":
                raise WriteError(f"table {table!r} already exists")
            self.session.execute(f"DROP TABLE {_qt(table)}")

        col_names = {c.name for c in columns}
        fragments: list[str] = []
        for c in columns:
            if not _SAFE_IDENT.match(c.name):
                raise WriteError(f"ensure_table: invalid column name {c.name!r}")
            spec = normalize_db_type(c.type or "")
            rendered = render_canonical(spec, dialect="cassandra")
            fragments.append(f'"{c.name}" {rendered}')

        # Cassandra requires a PRIMARY KEY in every CREATE TABLE. Use the
        # caller's key when given (upsert); otherwise fall back to the
        # first column so auto-create still produces a valid table.
        pk = primary_key or [columns[0].name]
        for k in pk:
            if not _SAFE_IDENT.match(k):
                raise WriteError(f"ensure_table: invalid primary key column {k!r}")
            if k not in col_names:
                raise WriteError(f"ensure_table: primary key column {k!r} not in columns")
        pk_list = ", ".join(f'"{k}"' for k in pk)
        fragments.append(f"PRIMARY KEY ({pk_list})")

        ddl = f"CREATE TABLE {_qt(table)} ({', '.join(fragments)})"
        self.session.execute(ddl)

    # ---------- SqlExecutor (ADR-0035) -------------------------------------

    def execute_statement(self, statement: str) -> int:
        try:
            self.session.execute(statement)
        except Exception as exc:
            raise WriteError(f"cassandra execute_statement failed: {exc}") from exc
        # CQL DML doesn't report affected-row counts.
        return 0

    # ---------- BatchSource -------------------------------------------------

    def read(
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]:
        if query is None:
            raise ReadError("CassandraConnector.read requires a CQL query")
        try:
            rows = self.session.execute(query)
            for r in rows:
                # named_tuple_factory rows expose ``_asdict`` / ``_fields``.
                if hasattr(r, "_asdict"):
                    data = dict(r._asdict())
                elif hasattr(r, "_fields"):
                    data = {f: getattr(r, f) for f in r._fields}
                else:
                    data = dict(r)
                yield Record(data=data, metadata={"source": "cassandra"})
        except Exception as exc:
            raise ReadError(f"cassandra read failed: {exc}") from exc

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
            raise WriteError("CassandraConnector.write requires 'table'")
        if mode not in ("append", "overwrite", "upsert"):
            raise WriteError(
                f"unknown write mode: {mode!r} (use 'append', 'overwrite', or 'upsert'; "
                "Cassandra INSERT always replaces by primary key)"
            )

        it = iter(records)
        first = next(it, None)
        try:
            if pre_sql:
                self.session.execute(pre_sql)
            if first is None:
                return 0
            if mode == "overwrite":
                self.session.execute(f"TRUNCATE {_qt(table)}")

            columns = list(first.data.keys())
            col_list = ", ".join(_q(c) for c in columns)
            placeholders = ", ".join(["%s"] * len(columns))
            stmt = f"INSERT INTO {_qt(table)} ({col_list}) VALUES ({placeholders})"

            count = 0
            for r in [first, *it]:
                self.session.execute(stmt, tuple(r.data.get(c) for c in columns))
                count += 1
            return count
        except Exception as exc:
            raise WriteError(f"cassandra write failed: {exc}") from exc
