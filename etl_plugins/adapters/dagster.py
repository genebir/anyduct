"""Dagster adapter — provides ``etl_plugins_op`` and ``EtlPluginsResource``.

The op is constructed lazily; importing this module without ``dagster``
installed still succeeds. Access the public symbols to trigger the dagster
import.

Example::

    from dagster import job
    from etl_plugins.adapters.dagster import EtlPluginsResource, etl_plugins_op

    @job(resource_defs={"etl_plugins": EtlPluginsResource()})
    def my_job():
        etl_plugins_op()
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from etl_plugins.core.pipeline import RunResult
from etl_plugins.runtime.runner import run_pipeline_yaml

__all__ = ["EtlPluginsResource", "etl_plugins_op"]


def _build_resource_class() -> type[Any]:
    try:
        from dagster import ConfigurableResource  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - covered via importorskip
        raise ImportError(
            "dagster is required for etl_plugins.adapters.dagster. "
            "Install with: pip install 'etl-plugins[dagster]'"
        ) from exc

    class EtlPluginsResource(ConfigurableResource):  # type: ignore[misc, valid-type]
        """Dagster resource exposing connection config to the op."""

        connections_path: str | None = None

        def run(self, pipeline_yaml: str | Path) -> RunResult:
            return run_pipeline_yaml(pipeline_yaml, connections_path=self.connections_path)

    return EtlPluginsResource


def _build_op() -> Any:
    try:
        from dagster import In, op  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - covered via importorskip
        raise ImportError(
            "dagster is required for etl_plugins.adapters.dagster. "
            "Install with: pip install 'etl-plugins[dagster]'"
        ) from exc

    @op(
        required_resource_keys={"etl_plugins"},
        ins={"pipeline_yaml": In(str)},
    )
    def etl_plugins_op(context: Any, pipeline_yaml: str) -> dict[str, Any]:
        """Run a YAML-defined etl-plugins pipeline via the resource."""
        result: RunResult = context.resources.etl_plugins.run(pipeline_yaml)
        return {
            "run_id": result.run_id,
            "pipeline_name": result.pipeline_name,
            "success": result.success,
            "records_read": result.records_read,
            "records_written": result.records_written,
            "duration_seconds": result.duration_seconds,
        }

    return etl_plugins_op


def __getattr__(name: str) -> Any:
    if name == "EtlPluginsResource":
        cls = _build_resource_class()
        globals()["EtlPluginsResource"] = cls
        return cls
    if name == "etl_plugins_op":
        op = _build_op()
        globals()["etl_plugins_op"] = op
        return op
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
