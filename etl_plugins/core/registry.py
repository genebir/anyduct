"""Connector plugin registry. SPEC.md §4.3.

Built-in connectors are registered at import time via the ``@register`` decorator.
External packages are auto-discovered through the ``etl_plugins.connectors`` entry
point group.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.metadata import entry_points
from typing import ClassVar, TypeVar

from etl_plugins.core.connector import Connector
from etl_plugins.core.exceptions import RegistryError

C = TypeVar("C", bound=type[Connector])

ENTRY_POINT_GROUP = "etl_plugins.connectors"

logger = logging.getLogger(__name__)


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
            raise RegistryError(
                f"Connector '{name}' not registered. Available: {sorted(cls._registry.keys())}"
            )
        return cls._registry[name]

    @classmethod
    def list_connectors(cls) -> list[str]:
        cls._load_entry_points()
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
