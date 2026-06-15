"""Connection schema introspection backbone (ADR-0033).

Materializes a stored ``Connection`` into a live core ``Connector`` — exactly
like :class:`~anyduct_server.connections.tester.ConnectionTester` — and, when that
connector implements the optional core ``SchemaInspector`` capability, reads its
table / column metadata so the no-code builder can offer pickers ("click
instead of type").

Connectors that can't introspect (HTTP, Kafka, plain object stores) simply don't
implement ``SchemaInspector``; for those :class:`ConnectionInspector` reports
``supported=False`` and the UI falls back to free-text entry.

The core connector API is synchronous, so the blocking ``connect`` /
``list_*`` / ``close`` calls are offloaded to a worker thread via
:func:`asyncio.to_thread`, keeping the FastAPI event loop unblocked.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any

from anyduct_server.connections.tester import SecretResolutionError, _resolve
from anyduct_server.db.models import Connection
from etl_plugins.config.secrets import SecretBackend
from etl_plugins.core.inspect import SchemaInspector
from etl_plugins.core.registry import ConnectorRegistry


class InspectionUnsupportedError(Exception):
    """Raised when a connection's connector can't introspect its schema."""


@dataclass(frozen=True)
class ColumnOutcome:
    name: str
    type: str


def _build_inspector(klass: type, options: dict[str, Any]) -> Any:
    connector = klass(**options)
    if not isinstance(connector, SchemaInspector):
        raise InspectionUnsupportedError("connector type does not support schema introspection")
    return connector


def _list_tables_blocking(klass: type, options: dict[str, Any]) -> list[str]:
    connector = _build_inspector(klass, options)
    connector.connect()
    try:
        return list(connector.list_tables())
    finally:
        with contextlib.suppress(Exception):
            connector.close()


def _list_columns_blocking(klass: type, options: dict[str, Any], table: str) -> list[ColumnOutcome]:
    connector = _build_inspector(klass, options)
    connector.connect()
    try:
        return [ColumnOutcome(name=c.name, type=c.type) for c in connector.list_columns(table)]
    finally:
        with contextlib.suppress(Exception):
            connector.close()


class ConnectionInspector:
    """Resolve secrets, build a connector, read its schema metadata."""

    def __init__(self, backend: SecretBackend) -> None:
        self._backend = backend

    def _resolve_klass_options(self, connection: Connection) -> tuple[type, dict[str, Any]]:
        options = _resolve(connection.config_json, self._backend)
        if not isinstance(options, dict):
            raise ValueError("resolved config is not a JSON object")
        klass = ConnectorRegistry.get(connection.type)
        return klass, options

    async def list_tables(self, connection: Connection) -> list[str]:
        klass, options = self._resolve_klass_options(connection)
        return await asyncio.to_thread(_list_tables_blocking, klass, options)

    async def list_columns(self, connection: Connection, table: str) -> list[ColumnOutcome]:
        klass, options = self._resolve_klass_options(connection)
        return await asyncio.to_thread(_list_columns_blocking, klass, options, table)


__all__ = [
    "ColumnOutcome",
    "ConnectionInspector",
    "InspectionUnsupportedError",
    "SecretResolutionError",
]
