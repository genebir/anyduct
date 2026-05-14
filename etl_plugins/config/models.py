"""Pydantic config models for connections.yaml / pipelines/*.yaml. SPEC.md §5."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConnectionConfig(BaseModel):
    """One connection definition.

    ``type`` identifies the connector in ``ConnectorRegistry``. All other fields
    are connector-specific (host, port, account, bootstrap_servers, ...), so
    ``extra="allow"`` is intentional.
    """

    model_config = ConfigDict(extra="allow")

    type: str

    def options(self) -> dict[str, Any]:
        """Return all non-``type`` fields as a dict (use to instantiate a Connector)."""
        return self.model_dump(exclude={"type"})


class ConnectionsConfig(BaseModel):
    """Top-level structure of ``configs/connections.yaml``."""

    model_config = ConfigDict(extra="forbid")

    connections: dict[str, ConnectionConfig] = Field(default_factory=dict)


class SourceConfig(BaseModel):
    """Pipeline source definition. ``connection`` references a key in connections.yaml."""

    model_config = ConfigDict(extra="allow")

    connection: str
    query: str | None = None
    chunk_size: int = 10_000
    # topic, group_id, format 등은 extra=allow로 통과


class SinkConfig(BaseModel):
    """Pipeline sink definition."""

    model_config = ConfigDict(extra="allow")

    connection: str
    table: str | None = None
    mode: str = "append"  # append | overwrite | upsert
    key_columns: list[str] | None = None


class TransformConfig(BaseModel):
    """A single transform step. ``type`` dispatches to a transform implementation."""

    model_config = ConfigDict(extra="allow")

    type: str  # rename | cast | filter | python | ...


class RetryConfig(BaseModel):
    """Retry policy (used by Step 3 retry decorator)."""

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = 3
    backoff: str = "exponential"  # fixed | exponential
    initial_delay_seconds: float = 5.0
    max_delay_seconds: float | None = None


class MetricsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    namespace: str = "etl_plugins"


class TracingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    exporter: str = "otlp"


class ObservabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    tracing: TracingConfig = Field(default_factory=TracingConfig)


class BufferConfig(BaseModel):
    """Stream sink buffering policy."""

    model_config = ConfigDict(extra="forbid")

    max_records: int = 10_000
    max_seconds: float = 30.0


class CommitConfig(BaseModel):
    """Stream commit strategy (SPEC.md §5.5)."""

    model_config = ConfigDict(extra="forbid")

    strategy: str = "after_sink_flush"  # at_least_once | after_sink_flush | ...


class PipelineConfig(BaseModel):
    """Top-level structure of ``configs/pipelines/*.yaml``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    mode: str = "batch"  # batch | stream
    schedule: str | None = None
    source: SourceConfig
    transforms: list[TransformConfig] = Field(default_factory=list)
    sink: SinkConfig
    retry: RetryConfig | None = None
    observability: ObservabilityConfig | None = None
    commit: CommitConfig | None = None
