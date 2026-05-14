"""Airflow adapter — provides ``ETLPluginsOperator``.

The operator is constructed lazily so importing this module without
``apache-airflow`` installed still succeeds. Access ``ETLPluginsOperator`` to
trigger the actual airflow import.

Example DAG snippet::

    from datetime import datetime

    from airflow import DAG
    from etl_plugins.adapters.airflow import ETLPluginsOperator

    with DAG("etl_demo", start_date=datetime(2026, 1, 1)) as dag:
        ETLPluginsOperator(
            task_id="run_orders_to_dw",
            pipeline_yaml="configs/pipelines/orders_to_dw.yaml",
            connections="configs/connections.yaml",
        )
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from etl_plugins.core.pipeline import RunResult
from etl_plugins.runtime.runner import run_pipeline_yaml

__all__ = ["ETLPluginsOperator"]


def _build_operator_class() -> type[Any]:
    """Return ``ETLPluginsOperator``, a subclass of ``airflow.models.BaseOperator``.

    Raises ``ImportError`` (with a hint about the ``airflow`` extra) if
    apache-airflow is not installed.
    """
    try:
        from airflow.models import BaseOperator
    except ImportError as exc:  # pragma: no cover - exercised via importorskip
        raise ImportError(
            "apache-airflow is required for etl_plugins.adapters.airflow. "
            "Install with: pip install 'etl-plugins[airflow]'"
        ) from exc

    class ETLPluginsOperator(BaseOperator):  # type: ignore[misc, valid-type]
        """Airflow operator that runs a YAML-defined etl-plugins pipeline."""

        template_fields = ("pipeline_yaml", "connections")

        def __init__(
            self,
            *,
            pipeline_yaml: str | Path,
            connections: str | Path | None = None,
            env_file: str | Path | None = None,
            **kwargs: Any,
        ) -> None:
            super().__init__(**kwargs)
            self.pipeline_yaml = str(pipeline_yaml)
            self.connections = str(connections) if connections is not None else None
            self.env_file = str(env_file) if env_file is not None else None

        def execute(self, context: dict[str, Any]) -> dict[str, Any]:
            if self.env_file is not None:
                from etl_plugins.config.loader import load_dotenv

                load_dotenv(self.env_file)
            result: RunResult = run_pipeline_yaml(
                self.pipeline_yaml,
                connections_path=self.connections,
            )
            # Push a small dict to XCom so downstream tasks can react to it.
            return {
                "run_id": result.run_id,
                "pipeline_name": result.pipeline_name,
                "success": result.success,
                "records_read": result.records_read,
                "records_written": result.records_written,
                "duration_seconds": result.duration_seconds,
            }

    return ETLPluginsOperator


def __getattr__(name: str) -> Any:
    """Lazily build the operator class on first attribute access."""
    if name == "ETLPluginsOperator":
        cls = _build_operator_class()
        globals()["ETLPluginsOperator"] = cls
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
