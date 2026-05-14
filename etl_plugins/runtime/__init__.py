"""Pipeline runtime: build from YAML + manage connector lifecycle + run.

Composes the layers below it (core / config / connectors) into something
the CLI and orchestrator adapters can call.
"""

from etl_plugins.runtime.builder import (
    build_connector,
    build_connectors,
    build_pipeline,
    build_pipeline_from_yaml,
)
from etl_plugins.runtime.runner import run_pipeline_yaml
from etl_plugins.runtime.transforms import (
    BuiltinTransform,
    build_transform,
    register_transform,
)

__all__ = [
    "BuiltinTransform",
    "build_connector",
    "build_connectors",
    "build_pipeline",
    "build_pipeline_from_yaml",
    "build_transform",
    "register_transform",
    "run_pipeline_yaml",
]
