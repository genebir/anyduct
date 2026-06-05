"""Connector plugin registry. SPEC.md §4.3.

Built-in connectors are registered at import time via the ``@register`` decorator.
External packages are auto-discovered through the ``etl_plugins.connectors`` entry
point group.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from importlib.metadata import entry_points
from typing import ClassVar, TypeVar

from etl_plugins.core.connector import Connector
from etl_plugins.core.exceptions import RegistryError

C = TypeVar("C", bound=type[Connector])

ENTRY_POINT_GROUP = "etl_plugins.connectors"

logger = logging.getLogger(__name__)

#: Built-in connector module paths — used as a defence-in-depth
#: fallback when the installed package's ``entry_points`` metadata is
#: stale (Phase AAQ post-mortem 2026-05-29). The user-visible symptom
#: was ``RegistryError: Connector 'vertica' not registered`` after a
#: fresh edit to ``pyproject.toml`` because the dev server kept
#: running against the metadata snapshot it started with.
#:
#: Importing the module triggers ``@ConnectorRegistry.register(...)``
#: at module top-level, which populates the registry. The lookup logic
#: tries entry_points first, then this table, then gives up — so
#: external plugins (still discovered via entry_points only) keep
#: working unchanged.
_BUILTIN_MODULES: dict[str, str] = {
    "postgres": "etl_plugins.connectors.rdbms.postgres",
    "mysql": "etl_plugins.connectors.rdbms.mysql",
    "sqlite": "etl_plugins.connectors.rdbms.sqlite",
    "vertica": "etl_plugins.connectors.rdbms.vertica",
    "mssql": "etl_plugins.connectors.rdbms.mssql",
    "snowflake": "etl_plugins.connectors.rdbms.snowflake",
    "bigquery": "etl_plugins.connectors.rdbms.bigquery",
    "redshift": "etl_plugins.connectors.rdbms.redshift",
    "clickhouse": "etl_plugins.connectors.rdbms.clickhouse",
    "mongodb": "etl_plugins.connectors.nosql.mongodb",
    "dynamodb": "etl_plugins.connectors.nosql.dynamodb",
    "cassandra": "etl_plugins.connectors.nosql.cassandra",
    "redis": "etl_plugins.connectors.nosql.redis",
    "s3": "etl_plugins.connectors.object_storage.s3",
    "kafka": "etl_plugins.connectors.stream.kafka",
    "kinesis": "etl_plugins.connectors.stream.kinesis",
    "sqs": "etl_plugins.connectors.stream.sqs",
    "http": "etl_plugins.connectors.http.connector",
}


class ConnectorRegistry:
    """Process-wide registry of connector classes.

    Built-in registration::

        @ConnectorRegistry.register("postgres")
        class PostgresConnector(BatchSource, BatchSink): ...

    External plugin discovery (``pyproject.toml`` of the plugin package)::

        [project.entry-points."etl_plugins.connectors"]
        clickhouse = "my_pkg.clickhouse:ClickhouseConnector"
    """

    _registry: ClassVar[dict[str, type[Connector]]] = {}
    _entry_points_loaded: ClassVar[bool] = False

    @classmethod
    def register(cls, name: str, *, replace: bool = False) -> Callable[[C], C]:
        def deco(klass: C) -> C:
            if name in cls._registry and not replace:
                existing = cls._registry[name].__name__
                raise RegistryError(
                    f"Connector '{name}' already registered "
                    f"(existing: {existing}, new: {klass.__name__}). "
                    f"Pass replace=True to override."
                )
            cls._registry[name] = klass
            klass.name = name
            return klass

        return deco

    @classmethod
    def get(cls, name: str) -> type[Connector]:
        if name not in cls._registry:
            cls._load_entry_points()
        if name not in cls._registry:
            # Built-in fallback (Phase AAQ post-mortem) — if the
            # installed metadata is stale but the source module is
            # right there, import it explicitly so the decorator
            # registers the connector.
            cls._load_builtin(name)
        if name not in cls._registry:
            raise RegistryError(
                f"Connector '{name}' not registered. Available: {sorted(cls._registry.keys())}"
            )
        return cls._registry[name]

    @classmethod
    def list_connectors(cls) -> list[str]:
        cls._load_entry_points()
        cls._load_all_builtins()
        return sorted(cls._registry.keys())

    @classmethod
    def clear(cls) -> None:
        """Reset state — intended for tests only."""
        cls._registry.clear()
        cls._entry_points_loaded = False

    @classmethod
    def _load_entry_points(cls) -> None:
        if cls._entry_points_loaded:
            return
        cls._entry_points_loaded = True
        try:
            eps = entry_points(group=ENTRY_POINT_GROUP)
        except Exception as exc:
            logger.warning("entry_points() failed: %s", exc)
            return
        for ep in eps:
            if ep.name in cls._registry:
                continue
            try:
                klass = ep.load()
            except Exception as exc:
                # 한 플러그인의 실패가 전체 레지스트리를 깨지 않게 한다.
                logger.warning("failed to load connector plugin %s: %s", ep.name, exc)
                continue
            cls._registry[ep.name] = klass

    @classmethod
    def _load_builtin(cls, name: str) -> None:
        """Import the built-in module for ``name`` if known. The
        module's top-level decorator does the actual registration."""
        module_path = _BUILTIN_MODULES.get(name)
        if module_path is None:
            return
        try:
            importlib.import_module(module_path)
        except Exception as exc:
            # Soft-fail — caller surfaces a clean RegistryError. We log
            # so the operator can debug an ImportError (missing extra,
            # broken module, etc.).
            logger.warning("failed to load built-in connector %s: %s", name, exc)

    @classmethod
    def _load_all_builtins(cls) -> None:
        """Try every built-in once. Used by :meth:`list_connectors` so
        ``etlx list-connectors`` is exhaustive even on a stale-metadata
        install."""
        for name in _BUILTIN_MODULES:
            if name not in cls._registry:
                cls._load_builtin(name)
