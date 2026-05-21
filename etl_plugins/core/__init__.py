"""Core abstractions: Connector, Record, Schema, Registry, Context, Pipeline."""

from etl_plugins.core.asset import (
    AssetGraph,
    AssetKey,
    AssetLineage,
    AssetSpec,
    LineageEdge,
)
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
    SqlAction,
    Task,
    TransformFn,
)
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry
from etl_plugins.core.schema import Field, Schema
from etl_plugins.core.sql_exec import SqlExecutor

__all__ = [
    "AssetGraph",
    "AssetKey",
    "AssetLineage",
    "AssetSpec",
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
    "LineageEdge",
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
    "SqlAction",
    "SqlExecutor",
    "StreamSink",
    "StreamSource",
    "Task",
    "TaskError",
    "TransformError",
    "TransformFn",
    "WriteError",
    "max_cursor_value",
]
