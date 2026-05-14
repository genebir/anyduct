"""Pipeline + connector instantiation from config.

The composition root:

    1. Load ``connections.yaml`` → ``ConnectionsConfig``
    2. Load ``pipelines/<x>.yaml`` → ``PipelineConfig``
    3. Resolve each connection through :class:`ConnectorRegistry` and instantiate
       it with the ``options()`` dict.
    4. Build a :class:`Pipeline` with a single :class:`Task` matching the YAML.
    5. Return ``(pipeline, connectors_dict)`` — caller manages open/close
       (or use :func:`run_pipeline_yaml`).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from etl_plugins.config.loader import load_connections, load_pipeline
from etl_plugins.config.models import (
    ConnectionConfig,
    ConnectionsConfig,
    PipelineConfig,
)
from etl_plugins.config.secrets import SecretBackend
from etl_plugins.core.connector import Connector
from etl_plugins.core.exceptions import ConfigError
from etl_plugins.core.pipeline import Pipeline, Task
from etl_plugins.core.registry import ConnectorRegistry
from etl_plugins.runtime.transforms import build_transform


def build_connector(name: str, config: ConnectionConfig) -> Connector:
    """Instantiate the registered Connector class for ``config.type``."""
    klass = ConnectorRegistry.get(config.type)
    try:
        return klass(**config.options())
    except TypeError as exc:
        raise ConfigError(
            f"connection {name!r}: failed to construct {klass.__name__} "
            f"with {sorted(config.options())}: {exc}"
        ) from exc


def build_connectors(config: ConnectionsConfig) -> dict[str, Connector]:
    """Instantiate every connection defined in ``connections.yaml``."""
    return {name: build_connector(name, c) for name, c in config.connections.items()}


def build_pipeline(
    pipeline_config: PipelineConfig,
    connectors: dict[str, Connector] | None = None,
) -> tuple[Pipeline, dict[str, Connector]]:
    """Build a Pipeline from a PipelineConfig.

    ``connectors`` is the available set; missing source/sink connections raise
    :class:`ConfigError`.
    """
    if connectors is None:
        connectors = {}

    src = pipeline_config.source
    snk = pipeline_config.sink

    if src.connection not in connectors:
        raise ConfigError(
            f"pipeline {pipeline_config.name!r}: source connection "
            f"{src.connection!r} not in available connectors {sorted(connectors)}"
        )
    if snk.connection not in connectors:
        raise ConfigError(
            f"pipeline {pipeline_config.name!r}: sink connection "
            f"{snk.connection!r} not in available connectors {sorted(connectors)}"
        )

    task = Task(
        name=pipeline_config.name,
        source=src.connection,
        query=src.query,
        source_options=src.model_dump(exclude={"connection", "query"}),
        sink=snk.connection,
        sink_table=snk.table,
        sink_mode=snk.mode,
        sink_key_columns=snk.key_columns,
        sink_options=snk.model_dump(exclude={"connection", "table", "mode", "key_columns"}),
    )
    for tc in pipeline_config.transforms:
        task.transform(build_transform(tc))

    commit_strategy = (
        pipeline_config.commit.strategy if pipeline_config.commit else "after_sink_flush"
    )
    pipeline = Pipeline(
        name=pipeline_config.name,
        mode=pipeline_config.mode,
        commit_strategy=commit_strategy,
    )
    pipeline.add(task)
    return pipeline, connectors


def build_pipeline_from_yaml(
    pipeline_path: str | Path,
    *,
    connections_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    secret_backend: SecretBackend | None = None,
    extra_connectors: dict[str, Connector] | None = None,
) -> tuple[Pipeline, dict[str, Connector]]:
    """Load YAML config and instantiate the Pipeline + its connectors.

    Parameters
    ----------
    pipeline_path
        Path to ``configs/pipelines/<x>.yaml``.
    connections_path
        Path to ``configs/connections.yaml``. If None, no connections are
        loaded — callers must provide ``extra_connectors``.
    env, secret_backend
        Forwarded to the YAML loader for ``${VAR}`` and ``!secret`` resolution.
    extra_connectors
        Pre-instantiated connectors (e.g. mocks for tests). Merged on top of
        whatever ``connections_path`` produces.
    """
    pc = load_pipeline(pipeline_path, env=env, secret_backend=secret_backend)
    connectors: dict[str, Connector] = {}
    if connections_path is not None:
        cc = load_connections(connections_path, env=env, secret_backend=secret_backend)
        connectors.update(build_connectors(cc))
    if extra_connectors:
        connectors.update(extra_connectors)
    return build_pipeline(pc, connectors)


__all__ = [
    "build_connector",
    "build_connectors",
    "build_pipeline",
    "build_pipeline_from_yaml",
]


# Type-only re-export to silence unused-import lint in some IDEs without changing behaviour
_PipelineAlias = Pipeline
_AnyAlias = Any
