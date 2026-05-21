"""Core abstractions: Connector, Record, Schema, Registry, Context, Pipeline."""

from etl_plugins.core.connector import (
    BatchSink,
    BatchSource,
    Connector,
    StreamSink,
    StreamSource,
)
from etl_plugins.core.context import Context
from etl_plugins.core.cursor import (
    Cursor,
    CursorState,
    CursorValue,
    FileCursorState,
    InMemoryCursorState,
    max_cursor_value,
)
from etl_plugins.core.exceptions import (
    ConfigError,
    ConnectError,
    ConnectorError,
    ETLError,
    PipelineError,
    ReadError,
    RecordError,
    RegistryError,
    SecretError,
    TaskError,
    TransformError,
    WriteError,
)
from etl_plugins.core.inspect import ColumnInfo, SchemaInspector
from etl_plugins.core.pipeline import (
    BranchRule,
    GraphEdge,
    GraphNode,
    Hook,
    Pipeline,
    RunResult,
    SinkSpec,
    Task,
    TransformFn,
)
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry
from etl_plugins.core.schema import Field, Schema

__all__ = [
    "BatchSink",
    "BatchSource",
    "BranchRule",
    "ColumnInfo",
    "ConfigError",
    "ConnectError",
    "Connector",
    "ConnectorError",
    "ConnectorRegistry",
    "Context",
    "Cursor",
    "CursorState",
    "CursorValue",
    "ETLError",
    "Field",
    "FileCursorState",
    "GraphEdge",
    "GraphNode",
    "Hook",
    "InMemoryCursorState",
    "Pipeline",
    "PipelineError",
    "ReadError",
    "Record",
    "RecordError",
    "RegistryError",
    "RunResult",
    "Schema",
    "SchemaInspector",
    "SecretError",
    "SinkSpec",
    "StreamSink",
    "StreamSource",
    "Task",
    "TaskError",
    "TransformError",
    "TransformFn",
    "WriteError",
    "max_cursor_value",
]
