"""High-level run helpers that manage connector lifecycle around Pipeline.run."""

from __future__ import annotations

import contextlib
import inspect
from collections.abc import Mapping
from pathlib import Path

from etl_plugins.config.secrets import SecretBackend
from etl_plugins.core.connector import Connector
from etl_plugins.core.context import Context
from etl_plugins.core.pipeline import RunResult
from etl_plugins.runtime.builder import build_pipeline_from_yaml
from etl_plugins.runtime.templating import RuntimeContext


def run_pipeline_yaml(
    pipeline_path: str | Path,
    *,
    connections_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    secret_backend: SecretBackend | None = None,
    extra_connectors: dict[str, Connector] | None = None,
    context: Context | None = None,
    runtime_context: RuntimeContext | None = None,
) -> RunResult:
    """Load YAML → build Pipeline + Connectors → open → run → close → return result.

    Connectors are opened with ``connect()`` before the run and closed with
    ``close()`` afterwards (even on exception). ``runtime_context`` enables
    the ``{{ ds }}`` / ``{{ params.x }}`` templating layer (자유도 1단계).
    """
    pipeline, connectors = build_pipeline_from_yaml(
        pipeline_path,
        connections_path=connections_path,
        env=env,
        secret_backend=secret_backend,
        extra_connectors=extra_connectors,
        runtime_context=runtime_context,
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


async def arun_stream_pipeline_yaml(
    pipeline_path: str | Path,
    *,
    connections_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    secret_backend: SecretBackend | None = None,
    extra_connectors: dict[str, Connector] | None = None,
    context: Context | None = None,
    stop_after_records: int | None = None,
    stop_after_seconds: float | None = None,
) -> RunResult:
    """Async stream-mode version of :func:`run_pipeline_yaml`.

    Connectors are ``connect()``ed, then ``Pipeline.arun_stream`` is awaited,
    then connectors are closed — preferring ``aclose()`` when available
    (matches Kafka's lazy producer/consumer lifecycle).
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
        return await pipeline.arun_stream(
            context=context,
            connectors=connectors,
            stop_after_records=stop_after_records,
            stop_after_seconds=stop_after_seconds,
        )
    finally:
        for c in connectors.values():
            aclose = getattr(c, "aclose", None)
            if callable(aclose) and inspect.iscoroutinefunction(aclose):
                with contextlib.suppress(Exception):
                    await aclose()
            else:
                with contextlib.suppress(Exception):
                    c.close()


__all__ = ["arun_stream_pipeline_yaml", "run_pipeline_yaml"]
