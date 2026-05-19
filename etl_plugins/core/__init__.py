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
from etl_plugins.core.pipeline import Hook, Pipeline, RunResult, Task, TransformFn
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry
from etl_plugins.core.schema import Field, Schema

__all__ = [
    "BatchSink",
    "BatchSource",
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
    "SecretError",
    "StreamSink",
    "StreamSource",
    "Task",
    "TaskError",
    "TransformError",
    "TransformFn",
    "WriteError",
    "max_cursor_value",
]
