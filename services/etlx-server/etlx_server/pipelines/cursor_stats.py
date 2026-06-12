"""Cursor-range statistics for partitioned backfill (ADR-0095 follow-up).

``MIN`` / ``MAX`` / ``COUNT`` over the pipeline source's ``cursor_column``,
so the Backfill dialog can *suggest* split boundaries. ADR-0095 rejected
server-side auto-splitting (arithmetic equal windows skew on gappy ids /
bursty time ranges) — this keeps that posture: the server only reports
the range; the operator sees, edits, and confirms the boundaries.

Mirrors :mod:`etlx_server.pipelines.dlq_preview`'s shape: a bounded
read-only query against the user's source connection, opened and closed
inside a worker thread, ``available=False`` + machine ``reason`` when the
pipeline/connection can't answer. Read-only; no audit.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.models import ConnectionConfig, PipelineConfig, SourceConfig
from etl_plugins.config.secrets import SecretBackend
from etl_plugins.config.variables import resolve_config_variables
from etl_plugins.core.connector import BatchSource
from etl_plugins.core.exceptions import ConfigError, RegistryError, SecretError
from etl_plugins.runtime.builder import build_connector
from etlx_server.db.models import Pipeline, PipelineVersion
from etlx_server.pipelines.dlq_preview import _SQL_READABLE_TYPES
from etlx_server.pipelines.runtime import (
    load_connections_by_name,
    resolve_placeholders,
)
from etlx_server.variables.repository import WorkspaceVariableRepository

_SAFE_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# The stats statement wraps the source query as a subselect — CQL
# (cassandra) has no subqueries, so it can't answer this.
_STATS_SQL_TYPES = _SQL_READABLE_TYPES - {"cassandra"}


@dataclass(frozen=True)
class CursorStats:
    """``available=True`` carries min/max/count; otherwise ``reason`` is a
    stable machine code: ``invalid_config`` / ``no_cursor`` /
    ``unsafe_column`` / ``connection_missing`` / ``source_not_readable`` /
    ``connection_build_failed`` / ``read_failed`` / ``empty``."""

    available: bool
    reason: str | None = None
    connection: str | None = None
    connector_type: str | None = None
    cursor_column: str | None = None
    min_value: Any = None
    max_value: Any = None
    row_count: int | None = None
    # Distribution-based split suggestion (2026-06-12): the upper bound of
    # each of N equal-ROW-COUNT buckets (NTILE) — the proper skew-proof
    # boundaries the dialog offers. ``None`` when not requested or the
    # dialect/query couldn't answer (best-effort).
    quantiles: list[Any] | None = None
    error: str | None = None


class CursorStatsService:
    def __init__(self, session: AsyncSession, backend: SecretBackend) -> None:
        self._session = session
        self._backend = backend

    async def stats(
        self, pipeline: Pipeline, version: PipelineVersion, *, windows: int = 0
    ) -> CursorStats:
        global_vars = await WorkspaceVariableRepository(self._session).as_dict(
            workspace_id=pipeline.workspace_id
        )
        try:
            cfg_dict = resolve_config_variables(version.config_json, extra=global_vars)
            cfg = PipelineConfig.model_validate(cfg_dict)
        except (ConfigError, ValidationError) as e:
            return CursorStats(available=False, reason="invalid_config", error=str(e))

        source = self._cursored_source(cfg)
        if source is None or not source.cursor_column or not source.query:
            return CursorStats(available=False, reason="no_cursor")
        column = source.cursor_column
        if not _SAFE_COLUMN_RE.match(column):
            return CursorStats(available=False, reason="unsafe_column", cursor_column=column)

        rows = await load_connections_by_name(
            self._session, workspace_id=pipeline.workspace_id, names=[source.connection]
        )
        row = rows.get(source.connection)
        if row is None:
            return CursorStats(
                available=False,
                reason="connection_missing",
                connection=source.connection,
                cursor_column=column,
            )
        if row.type not in _STATS_SQL_TYPES:
            return CursorStats(
                available=False,
                reason="source_not_readable",
                connection=source.connection,
                connector_type=row.type,
                cursor_column=column,
            )

        try:
            resolved = resolve_placeholders(row.config_json, self._backend)
            conn_cfg = ConnectionConfig.model_validate({"type": row.type, **resolved})
            connector = build_connector(source.connection, conn_cfg)
        except (ConfigError, RegistryError, SecretError, ValidationError) as e:
            return CursorStats(
                available=False,
                reason="connection_build_failed",
                connection=source.connection,
                connector_type=row.type,
                cursor_column=column,
                error=str(e),
            )
        if not isinstance(connector, BatchSource):
            return CursorStats(
                available=False,
                reason="source_not_readable",
                connection=source.connection,
                connector_type=row.type,
                cursor_column=column,
            )

        stmt = (
            f"SELECT MIN({column}) AS lo, MAX({column}) AS hi, COUNT(*) AS n "
            f"FROM ({source.query}) AS __cursor_stats"
        )
        try:
            data = await asyncio.to_thread(self._read_one, connector, stmt)
        except Exception as e:  # connector-specific failures vary widely
            return CursorStats(
                available=False,
                reason="read_failed",
                connection=source.connection,
                connector_type=row.type,
                cursor_column=column,
                error=str(e),
            )
        # Alias case differs by dialect (snowflake upper-cases unquoted
        # aliases) — normalise keys before reading them.
        folded = {str(k).lower(): v for k, v in (data or {}).items()}
        count = folded.get("n")
        if not count:
            return CursorStats(
                available=False,
                reason="empty",
                connection=source.connection,
                connector_type=row.type,
                cursor_column=column,
                row_count=0,
            )
        quantiles: list[Any] | None = None
        if windows >= 2:
            # Equal-row-count bucket boundaries via NTILE — portable across
            # the window-function dialects in _STATS_SQL_TYPES. Best-effort:
            # an old engine without NTILE just loses the suggestion.
            ntile_stmt = (
                f"SELECT MAX(__c) AS hi FROM ("
                f"SELECT {column} AS __c, NTILE({int(windows)}) OVER (ORDER BY {column}) AS __b "
                f"FROM ({source.query}) AS __q) AS __t GROUP BY __b ORDER BY MAX(__c)"
            )
            try:
                quantiles = await asyncio.to_thread(self._read_column, connector, ntile_stmt)
            except Exception:
                quantiles = None
        return CursorStats(
            available=True,
            connection=source.connection,
            connector_type=row.type,
            cursor_column=column,
            min_value=folded.get("lo"),
            max_value=folded.get("hi"),
            row_count=int(count),
            quantiles=quantiles,
        )

    @staticmethod
    def _cursored_source(cfg: PipelineConfig) -> SourceConfig | None:
        """First source declaring a cursor_column (single-task or task-DAG;
        graphs don't support cursor backfill)."""
        if cfg.source is not None and cfg.source.cursor_column:
            return cfg.source
        for task in cfg.tasks:
            if task.source.cursor_column:
                return task.source
        return None

    @staticmethod
    def _read_column(connector: BatchSource, stmt: str) -> list[Any]:
        """All rows' first value — used for the NTILE quantile list."""
        connector.connect()
        try:
            out: list[Any] = []
            for rec in connector.read(query=stmt, chunk_size=128):
                values = list(rec.data.values())
                if values:
                    out.append(values[0])
            return out
        finally:
            with contextlib.suppress(Exception):
                connector.close()

    @staticmethod
    def _read_one(connector: BatchSource, stmt: str) -> dict[str, Any] | None:
        connector.connect()
        try:
            for rec in connector.read(query=stmt, chunk_size=1):
                return dict(rec.data)
            return None
        finally:
            with contextlib.suppress(Exception):
                connector.close()


__all__ = ["CursorStats", "CursorStatsService"]
