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
from etl_plugins.runtime.column_lineage import derive_column_lineage
from etl_plugins.runtime.graph import node_dependencies, to_graph, topological_order
from etl_plugins.runtime.runner import arun_stream_pipeline_yaml, run_pipeline_yaml
from etl_plugins.runtime.templating import (
    RuntimeContext,
    has_template,
    render_config_templates,
    render_templates,
)
from etl_plugins.runtime.transforms import (
    BuiltinTransform,
    build_transform,
    register_transform,
)

__all__ = [
    "BuiltinTransform",
    "RuntimeContext",
    "arun_stream_pipeline_yaml",
    "build_connector",
    "build_connectors",
    "build_pipeline",
    "build_pipeline_from_yaml",
    "build_transform",
    "derive_column_lineage",
    "has_template",
    "node_dependencies",
    "register_transform",
    "render_config_templates",
    "render_templates",
    "run_pipeline_yaml",
    "to_graph",
    "topological_order",
]
