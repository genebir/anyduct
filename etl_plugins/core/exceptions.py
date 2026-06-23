"""Exception hierarchy for ETL Plugins."""

from __future__ import annotations


class ETLError(Exception):
    """Base exception for all ETL Plugins errors."""


class ConfigError(ETLError):
    """Invalid configuration."""


class RegistryError(ETLError):
    """Connector registry error (duplicate registration, unknown name, plugin load failure)."""


class ConnectorError(ETLError):
    """Connector-level error."""


class ConnectError(ConnectorError):
    """Failed to establish or maintain a connection.

    Distinct from Python's built-in ConnectionError to avoid shadowing.
    """


class ReadError(ConnectorError):
    """Failed to read from a source."""


class WriteError(ConnectorError):
    """Failed to write to a sink."""


class RecordError(ETLError):
    """Invalid Record payload or metadata."""


class PipelineError(ETLError):
    """Pipeline execution error."""


class TaskError(PipelineError):
    """Task-level execution error (missing source/sink, type mismatch, etc.)."""


class TaskTimeoutError(TaskError):
    """A task exceeded its ``timeout_seconds`` (자유도 2단계). A subclass of
    TaskError so a configured ``retry`` retries a slow task by default; the
    deadline is checked at record/chunk boundaries (see Pipeline)."""


class TransformError(PipelineError):
    """A transform callable raised."""


class AssertionFailedError(TransformError):
    """A data-quality assertion (``assert`` transform, ADR-0041 K1) failed.

    Carries the rendered assertion message + a short repr of the offending
    record's data so the run row's ``error_message`` lands something
    actionable instead of a stack frame.
    """


class SecretError(ETLError):
    """Failed to resolve a secret via the configured secret backend."""
