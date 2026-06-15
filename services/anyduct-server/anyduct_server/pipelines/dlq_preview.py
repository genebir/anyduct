"""DLQ record preview (Phase DLQ-1, ADR-0075).

Reads a bounded sample of the records a pipeline routed to its
dead-letter queue, so the UI can answer *"what actually failed?"* — not
just *"how many"* (the run-detail metric, Phase AFB).

The DLQ is a user-configured sink (connection + table). When that sink
is also a :class:`BatchSource` (every RDBMS connector is), we can read
the rows back. Non-readable sinks (a Kafka topic, an HTTP endpoint, any
write-only connector) report ``available=False`` with a machine
``reason`` the UI renders as guidance ("preview unavailable for this
sink type").

Scope mirrors the note in :mod:`anyduct_server.pipelines.dry_run`: this is a
*bounded* read (``LIMIT``/``TOP``), opened and closed inside a worker
thread, not a streaming / long-transaction path. Read-only; no audit.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.db.models import Pipeline, PipelineVersion
from anyduct_server.pipelines.runtime import (
    load_connections_by_name,
    resolve_placeholders,
)
from anyduct_server.variables.repository import WorkspaceVariableRepository
from etl_plugins.config.models import ConnectionConfig, PipelineConfig
from etl_plugins.config.secrets import SecretBackend
from etl_plugins.config.variables import resolve_config_variables
from etl_plugins.core.connector import BatchSource
from etl_plugins.core.exceptions import ConfigError, RegistryError, SecretError
from etl_plugins.runtime.builder import build_connector

# A DLQ table name gets spliced into a ``SELECT`` for the preview. Restrict
# it to a plain identifier or schema-qualified ``schema.table`` so an odd
# config value can't smuggle SQL into the query. The value comes from the
# workspace's own pipeline config, but defense-in-depth is cheap.
_SAFE_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")

# MSSQL has no ``LIMIT`` — it uses ``SELECT TOP n``. Everything else we
# ship (sqlite / postgres / mysql / vertica) uses ``LIMIT``.
_TOP_DIALECTS = {"mssql"}

# Only RDBMS connectors take a SQL ``SELECT`` (what the preview builds).
# A non-SQL sink (S3 object store, Kafka topic, HTTP endpoint) may still
# be a ``BatchSource``, but feeding it ``SELECT * FROM t`` would either
# error cryptically or misbehave — so we gate on a known-SQL allow-list
# and report such sinks as ``sink_not_readable`` (the UI says "query the
# sink directly"). Reading an object-store DLQ is a separate slice.
_SQL_READABLE_TYPES = {
    "sqlite",
    "postgres",
    "mysql",
    "vertica",
    "mssql",
    "snowflake",
    "bigquery",
    "redshift",
    "clickhouse",
    "cassandra",
}


@dataclass(frozen=True)
class DlqPreview:
    """Bundled preview outcome.

    ``available`` is true only when records were actually read. When
    false, ``reason`` is a stable machine code the UI maps to a message:
    ``no_dlq`` / ``stream_dlq`` / ``no_table`` / ``unsafe_table`` /
    ``connection_missing`` / ``connection_build_failed`` /
    ``sink_not_readable`` / ``invalid_config`` / ``read_failed``.
    """

    available: bool
    reason: str | None = None
    connection: str | None = None
    table: str | None = None
    connector_type: str | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None  # raw message when reason == read_failed/build


class DlqPreviewService:
    def __init__(self, session: AsyncSession, backend: SecretBackend) -> None:
        self._session = session
        self._backend = backend

    async def preview(
        self,
        pipeline: Pipeline,
        version: PipelineVersion,
        *,
        limit: int,
    ) -> DlqPreview:
        global_vars = await WorkspaceVariableRepository(self._session).as_dict(
            workspace_id=pipeline.workspace_id
        )
        try:
            cfg_dict = resolve_config_variables(version.config_json, extra=global_vars)
            cfg = PipelineConfig.model_validate(cfg_dict)
        except (ConfigError, ValidationError) as e:
            return DlqPreview(available=False, reason="invalid_config", error=str(e))

        if cfg.dlq is None:
            return DlqPreview(available=False, reason="no_dlq")
        dlq = cfg.dlq
        if dlq.table is None:
            # Stream DLQ routes to a topic — there's no table to read back.
            return DlqPreview(available=False, reason="stream_dlq", connection=dlq.connection)
        if not _SAFE_TABLE_RE.match(dlq.table):
            return DlqPreview(
                available=False,
                reason="unsafe_table",
                connection=dlq.connection,
                table=dlq.table,
            )

        rows = await load_connections_by_name(
            self._session, workspace_id=pipeline.workspace_id, names=[dlq.connection]
        )
        row = rows.get(dlq.connection)
        if row is None:
            return DlqPreview(
                available=False,
                reason="connection_missing",
                connection=dlq.connection,
                table=dlq.table,
            )

        # Gate on a known SQL allow-list *before* building the connector:
        # cheaper (no heavy boto3/aiokafka import) and avoids feeding SQL
        # to a sink that doesn't speak it.
        if row.type not in _SQL_READABLE_TYPES:
            return DlqPreview(
                available=False,
                reason="sink_not_readable",
                connection=dlq.connection,
                table=dlq.table,
                connector_type=row.type,
            )

        try:
            resolved = resolve_placeholders(row.config_json, self._backend)
            conn_cfg = ConnectionConfig.model_validate({"type": row.type, **resolved})
            connector = build_connector(dlq.connection, conn_cfg)
        except (ConfigError, RegistryError, SecretError, ValidationError) as e:
            return DlqPreview(
                available=False,
                reason="connection_build_failed",
                connection=dlq.connection,
                table=dlq.table,
                connector_type=row.type,
                error=str(e),
            )

        if not isinstance(connector, BatchSource):
            # Kafka topic / HTTP / write-only sink — nothing to read.
            return DlqPreview(
                available=False,
                reason="sink_not_readable",
                connection=dlq.connection,
                table=dlq.table,
                connector_type=row.type,
            )

        query = self._preview_query(row.type, dlq.table, limit)
        try:
            records = await asyncio.to_thread(self._read, connector, query, limit)
        except Exception as e:  # connector-specific read failures vary widely
            return DlqPreview(
                available=False,
                reason="read_failed",
                connection=dlq.connection,
                table=dlq.table,
                connector_type=row.type,
                error=str(e),
            )
        return DlqPreview(
            available=True,
            connection=dlq.connection,
            table=dlq.table,
            connector_type=row.type,
            records=records,
        )

    @staticmethod
    def _preview_query(conn_type: str, table: str, limit: int) -> str:
        # ``limit`` is a server-bounded int; ``table`` passed _SAFE_TABLE_RE.
        if conn_type in _TOP_DIALECTS:
            return f"SELECT TOP {limit} * FROM {table}"
        return f"SELECT * FROM {table} LIMIT {limit}"

    @staticmethod
    def _read(connector: BatchSource, query: str, limit: int) -> list[dict[str, Any]]:
        connector.connect()
        try:
            out: list[dict[str, Any]] = []
            for rec in connector.read(query=query, chunk_size=limit):
                out.append(dict(rec.data))
                if len(out) >= limit:
                    break
            return out
        finally:
            with contextlib.suppress(Exception):
                connector.close()


__all__ = ["DlqPreview", "DlqPreviewService"]
