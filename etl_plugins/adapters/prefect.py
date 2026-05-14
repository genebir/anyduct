"""Prefect adapter — provides ``run_etl_pipeline_flow`` and ``run_etl_pipeline_task``.

The flow / task are built lazily; importing this module without ``prefect``
installed still succeeds. Access the public symbols to trigger the prefect
import.

Example::

    from etl_plugins.adapters.prefect import run_etl_pipeline_flow

    run_etl_pipeline_flow(
        "configs/pipelines/orders_to_dw.yaml",
        connections="configs/connections.yaml",
    )
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from etl_plugins.core.pipeline import RunResult
from etl_plugins.runtime.runner import run_pipeline_yaml

__all__ = ["run_etl_pipeline_flow", "run_etl_pipeline_task"]


def _build_task() -> Any:
    try:
        from prefect import task  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - covered via importorskip
        raise ImportError(
            "prefect is required for etl_plugins.adapters.prefect. "
            "Install with: pip install 'etl-plugins[prefect]'"
        ) from exc

    @task(name="etl_plugins_run")
    def run_etl_pipeline_task(
        pipeline_yaml: str | Path,
        connections: str | Path | None = None,
    ) -> dict[str, Any]:
        result: RunResult = run_pipeline_yaml(pipeline_yaml, connections_path=connections)
        return {
            "run_id": result.run_id,
            "pipeline_name": result.pipeline_name,
            "success": result.success,
            "records_read": result.records_read,
            "records_written": result.records_written,
            "duration_seconds": result.duration_seconds,
        }

    return run_etl_pipeline_task


def _build_flow() -> Any:
    try:
        from prefect import flow  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - covered via importorskip
        raise ImportError(
            "prefect is required for etl_plugins.adapters.prefect. "
            "Install with: pip install 'etl-plugins[prefect]'"
        ) from exc

    inner_task = _build_task()

    @flow(name="etl_plugins_pipeline")
    def run_etl_pipeline_flow(
        pipeline_yaml: str | Path,
        connections: str | Path | None = None,
    ) -> dict[str, Any]:
        return inner_task(pipeline_yaml, connections)  # type: ignore[no-any-return]

    return run_etl_pipeline_flow


def __getattr__(name: str) -> Any:
    if name == "run_etl_pipeline_task":
        t = _build_task()
        globals()["run_etl_pipeline_task"] = t
        return t
    if name == "run_etl_pipeline_flow":
        f = _build_flow()
        globals()["run_etl_pipeline_flow"] = f
        return f
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
