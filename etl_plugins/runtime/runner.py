"""High-level run helpers that manage connector lifecycle around Pipeline.run."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from pathlib import Path

from etl_plugins.config.secrets import SecretBackend
from etl_plugins.core.connector import Connector
from etl_plugins.core.context import Context
from etl_plugins.core.pipeline import RunResult
from etl_plugins.runtime.builder import build_pipeline_from_yaml


def run_pipeline_yaml(
    pipeline_path: str | Path,
    *,
    connections_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    secret_backend: SecretBackend | None = None,
    extra_connectors: dict[str, Connector] | None = None,
    context: Context | None = None,
) -> RunResult:
    """Load YAML → build Pipeline + Connectors → open → run → close → return result.

    Connectors are opened with ``connect()`` before the run and closed with
    ``close()`` afterwards (even on exception).
    """
    pipeline, connectors = build_pipeline_from_yaml(
        pipeline_path,
        connections_path=connections_path,
        env=env,
        secret_backend=secret_backend,
        extra_connectors=extra_connectors,
    )

    for c in connectors.values():
        c.connect()

    try:
        return pipeline.run(context=context, connectors=connectors)
    finally:
        for c in connectors.values():
            # 한 커넥터 close 실패가 다른 커넥터 정리를 막지 않도록.
            with contextlib.suppress(Exception):
                c.close()


__all__ = ["run_pipeline_yaml"]
