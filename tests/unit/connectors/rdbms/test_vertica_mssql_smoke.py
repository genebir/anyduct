"""Driver-free smoke tests for Vertica + MSSQL connectors (Phase AAQ).

We can't spin up real Vertica / SQL Server inside unit tests, but we
*can* prove three things without a server:

1. Both connector classes load through the registry (defence in
   depth — covers the stale-``entry_points`` post-mortem path).
2. Both raise a *clear* ``ConnectError`` rather than a vague
   ``ImportError`` when the driver extra is missing.
3. Both expose the contract methods :class:`BatchSource` /
   :class:`BatchSink` / :class:`SchemaInspector` /
   :class:`SchemaWriter` require — caught at class-instantiation time
   rather than at the first ``write()``.
"""

from __future__ import annotations

import pytest

from etl_plugins.connectors.rdbms.mssql import MSSQLConnector
from etl_plugins.connectors.rdbms.vertica import VerticaConnector
from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError
from etl_plugins.core.inspect import SchemaInspector, SchemaWriter
from etl_plugins.core.registry import ConnectorRegistry

# ---------- registry round-trip ----------------------------------------


def test_vertica_resolves_through_registry() -> None:
    """Mirrors the user-facing path: web → server → ``ConnectorRegistry.get``."""
    klass = ConnectorRegistry.get("vertica")
    assert klass is VerticaConnector


def test_mssql_resolves_through_registry() -> None:
    klass = ConnectorRegistry.get("mssql")
    assert klass is MSSQLConnector


# ---------- contract surface area --------------------------------------


def test_vertica_implements_all_capability_protocols() -> None:
    """``isinstance`` checks against the runtime-checkable protocols
    that the migration / catalog / sql_exec features rely on."""
    c = VerticaConnector(host="x", database="x", user="u", password="p")
    # Cross-DB migration depends on SchemaInspector + SchemaWriter.
    assert isinstance(c, SchemaInspector)
    assert isinstance(c, SchemaWriter)
    # Pipeline run depends on BatchSource + BatchSink.
    assert isinstance(c, BatchSource)
    assert isinstance(c, BatchSink)


def test_mssql_implements_all_capability_protocols() -> None:
    c = MSSQLConnector(host="x", database="x", user="u", password="p")
    assert isinstance(c, SchemaInspector)
    assert isinstance(c, SchemaWriter)
    assert isinstance(c, BatchSource)
    assert isinstance(c, BatchSink)


# ---------- "not installed" surface message ----------------------------


def test_vertica_connect_error_when_driver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the operator skipped ``[vertica]`` we want a friendly,
    actionable ``ConnectError`` — *not* a raw ``ImportError`` or
    ``ModuleNotFoundError`` leaking out of the connector."""
    import sys

    # Pretend the driver module isn't importable.
    monkeypatch.setitem(sys.modules, "vertica_python", None)
    c = VerticaConnector(host="nowhere", database="x")
    with pytest.raises(ConnectError) as excinfo:
        c.connect()
    msg = str(excinfo.value)
    assert "vertica-python not installed" in msg
    assert "pip install" in msg  # the actionable hint


def test_mssql_connect_error_when_driver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "pymssql", None)
    c = MSSQLConnector(host="nowhere", database="x")
    with pytest.raises(ConnectError) as excinfo:
        c.connect()
    msg = str(excinfo.value)
    assert "pymssql not installed" in msg
    assert "pip install" in msg


# ---------- DDL safety guards still apply -------------------------------


def test_vertica_rejects_unsafe_table_identifier() -> None:
    from etl_plugins.core.exceptions import WriteError
    from etl_plugins.core.inspect import ColumnInfo

    c = VerticaConnector()
    with pytest.raises(WriteError, match="invalid table name"):
        c.ensure_table("orders; DROP", [ColumnInfo(name="id", type="INTEGER")])


def test_mssql_rejects_unsafe_table_identifier() -> None:
    from etl_plugins.core.exceptions import WriteError
    from etl_plugins.core.inspect import ColumnInfo

    c = MSSQLConnector()
    with pytest.raises(WriteError, match="invalid table name"):
        c.ensure_table("orders; DROP", [ColumnInfo(name="id", type="INTEGER")])
