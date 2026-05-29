"""Optional connector introspection capability (ADR-0033).

Connectors that can enumerate their schema implement :class:`SchemaInspector`
so the no-code builder can offer table + column pickers ("click instead of
type"). It's an *optional* capability — connectors that can't introspect (HTTP,
Kafka, plain object stores) simply don't implement it, and callers guard with
``isinstance(conn, SchemaInspector)`` (the Protocol is ``runtime_checkable``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ColumnInfo:
    """One column: ``name`` plus a connector-native ``type`` label (informational)."""

    name: str
    type: str


@runtime_checkable
class SchemaInspector(Protocol):
    """A connector that can list its tables and a table's columns.

    ``list_tables`` returns names in the connector's natural addressing form
    (e.g. ``"schema.table"`` for Postgres, bare ``"table"`` for SQLite) — the
    same string the builder writes into a sink's ``table`` field.
    """

    def list_tables(self) -> list[str]: ...

    def list_columns(self, table: str) -> list[ColumnInfo]: ...


@runtime_checkable
class SchemaWriter(Protocol):
    """A connector that can create a table from a column schema (Phase VV,
    2026-05-29).

    The dual of :class:`SchemaInspector` — *Inspector* reads the layout
    of an existing table; *Writer* creates a table from a desired layout.
    Used by the cross-DB replication path: the source's
    ``list_columns`` output is mapped through
    :mod:`etl_plugins.core.type_mapping` and handed to the sink's
    ``ensure_table`` so the sink table exists before the first
    ``write``. Optional capability — connectors that can't issue DDL
    (HTTP, Kafka, S3) don't implement it.
    """

    def ensure_table(
        self,
        table: str,
        columns: list[ColumnInfo],
        *,
        if_exists: str = "skip",  # "skip" | "drop" | "error"
    ) -> None: ...


__all__ = ["ColumnInfo", "SchemaInspector", "SchemaWriter"]
