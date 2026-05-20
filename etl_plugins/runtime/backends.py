"""Execution backends (ADR-0031).

A pipeline is *defined* once (as a :class:`PipelineConfig`) and *executed* by a
backend. The default ``local`` backend is the in-process, row-by-row streaming
engine (current behaviour). Future backends (``spark``) compile the same DAG to
a distributed engine for TB-scale pushdown — selected via ``PipelineConfig.engine``.

This module owns only the abstraction + registry + the ``local`` backend; the
Spark backend lands in a later slice. Keeping config unaware of the registry
(it stores ``engine`` as a plain string) preserves the layer boundary — an
unknown engine is rejected here, at run time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from etl_plugins.config.models import ConnectionConfig, PipelineConfig
from etl_plugins.core.connector import Connector
from etl_plugins.core.context import Context
from etl_plugins.core.cursor import CursorValue
from etl_plugins.core.exceptions import ConfigError
from etl_plugins.core.pipeline import RunResult
from etl_plugins.runtime.builder import build_pipeline


class ExecutionBackend(ABC):
    """Runs a :class:`PipelineConfig` to a :class:`RunResult`.

    Backends consume different inputs (ADR-0031): the ``local`` engine uses
    pre-built, caller-managed connector instances (``connectors``); native
    engines like Spark read/write themselves and need connection *configs*
    (``connections``). Both are passed; each backend uses what it needs.
    """

    name: ClassVar[str]

    @abstractmethod
    def run(
        self,
        config: PipelineConfig,
        *,
        connectors: dict[str, Connector] | None = None,
        connections: dict[str, ConnectionConfig] | None = None,
        context: Context | None = None,
        cursor_from: CursorValue = None,
        cursor_to: CursorValue = None,
    ) -> RunResult: ...


class LocalBackend(ExecutionBackend):
    """In-process row-streaming execution — wraps :func:`build_pipeline` + ``Pipeline.run``."""

    name = "local"

    def run(
        self,
        config: PipelineConfig,
        *,
        connectors: dict[str, Connector] | None = None,
        connections: dict[str, ConnectionConfig] | None = None,
        context: Context | None = None,
        cursor_from: CursorValue = None,
        cursor_to: CursorValue = None,
    ) -> RunResult:
        if connectors is None:
            raise ConfigError("local backend requires pre-built 'connectors'")
        pipeline, conns = build_pipeline(config, connectors)
        return pipeline.run(
            context=context,
            connectors=conns,
            cursor_from=cursor_from,
            cursor_to=cursor_to,
        )


_BACKENDS: dict[str, ExecutionBackend] = {}


def register_backend(backend: ExecutionBackend) -> None:
    """Register an execution backend under ``backend.name`` (replaces an existing)."""
    _BACKENDS[backend.name] = backend


def get_backend(name: str) -> ExecutionBackend:
    """Look up a registered backend; raise :class:`ConfigError` if unknown.

    The ``spark`` backend is registered lazily on first use so importing it (and
    transitively touching pyspark only when it *runs*) stays off the default path
    and avoids an import cycle with this module.
    """
    if name == "spark" and "spark" not in _BACKENDS:
        from etl_plugins.runtime.spark.backend import SparkBackend

        register_backend(SparkBackend())
    backend = _BACKENDS.get(name)
    if backend is None:
        raise ConfigError(f"unknown execution engine {name!r} (registered: {sorted(_BACKENDS)})")
    return backend


def run_config(
    config: PipelineConfig,
    *,
    connectors: dict[str, Connector] | None = None,
    connections: dict[str, ConnectionConfig] | None = None,
    engine: str | None = None,
    context: Context | None = None,
    cursor_from: CursorValue = None,
    cursor_to: CursorValue = None,
) -> RunResult:
    """Dispatch a pipeline to its execution backend (``engine`` or ``config.engine``)."""
    return get_backend(engine or config.engine).run(
        config,
        connectors=connectors,
        connections=connections,
        context=context,
        cursor_from=cursor_from,
        cursor_to=cursor_to,
    )


register_backend(LocalBackend())  # ``spark`` registers lazily in get_backend()


__all__ = [
    "ExecutionBackend",
    "LocalBackend",
    "get_backend",
    "register_backend",
    "run_config",
]
