"""``dataset_row_count`` sensor — fires when a table's row count is out of band.

The data-quality gate: complements assertion transforms (ADR-0041 K1)
by watching the *destination* side of a pipeline. Two complementary
shapes:

* ``min_rows`` — fire when count is below the floor. "Orders table is
  empty" / "feed produced fewer rows than expected" — catches a stuck
  upstream feed before downstream consumers notice.
* ``max_rows`` — fire when count is above the ceiling. "Unexpected
  burst into the staging table" — catches a feedback loop or upstream
  schema change duplicating rows.

Both bounds may be set; an out-of-band count in either direction
triggers. Optional ``where`` narrows the count to a slice — typical
use is ``"created_at > now() - interval '1 day'"`` to watch fresh
arrivals only.

Config (``config_json``):
    connection_id: str (UUID)
        References a workspace ``Connection`` whose connector is a
        :class:`BatchSource` (postgres / mysql / sqlite — the SQL
        connectors that can answer a ``SELECT COUNT(*)`` query). The
        sensor resolves ``${SECRET:<path>}`` placeholders the same way
        the worker does.
    table: str
        Fully-qualified table name (``schema.table`` or just
        ``table``). Used verbatim in ``SELECT COUNT(*) AS n FROM
        <table>``. No quoting / sanitisation here — the operator
        wrote the value, same trust model as a pipeline's source
        query.
    min_rows: int | null
        Trigger when the count is **less than** ``min_rows``. ``null``
        / omitted to disable the lower bound.
    max_rows: int | null
        Trigger when the count is **greater than** ``max_rows``.
        ``null`` / omitted to disable the upper bound.
    where: str | null
        Optional SQL fragment appended as ``WHERE <where>``. Trusted
        like ``table`` (operator-authored).

At least one of ``min_rows`` / ``max_rows`` must be set, else the
sensor would never fire (rejected at build time).

Soft-fail (no scheduler crash on a flaky DB):
    * Connection not found / not a BatchSource → triggered=False with
      error metadata.
    * Secret resolution failure                → triggered=False.
    * COUNT query raises                       → triggered=False with
      the error class.
Hard config errors raise :class:`ConfigError` at build time so a typo
becomes a 4xx on POST /sensors instead of a quiet "never fires" row.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import select

from etl_plugins.config.models import ConnectionConfig
from etl_plugins.core.connector import BatchSource
from etl_plugins.core.exceptions import ConfigError, SecretError
from etl_plugins.core.sensor import SensorBase, SensorResult, register_sensor
from etl_plugins.runtime.builder import build_connector
from etlx_server.db.models import Connection
from etlx_server.pipelines.runtime import resolve_placeholders
from etlx_server.sensors.context import (
    sensor_secret_backend,
    sensor_session_factory,
    sensor_workspace_id,
)


class DatasetRowCountSensor(SensorBase):
    """Polls ``SELECT COUNT(*)`` on a referenced table; fires when out of band."""

    def __init__(
        self,
        *,
        connection_id: UUID,
        table: str,
        min_rows: int | None,
        max_rows: int | None,
        where: str | None,
    ) -> None:
        if not table:
            raise ConfigError("dataset_row_count: 'table' must be a non-empty string")
        if min_rows is None and max_rows is None:
            raise ConfigError(
                "dataset_row_count: at least one of 'min_rows' / 'max_rows' is required "
                "(otherwise the sensor never fires)"
            )
        if min_rows is not None and min_rows < 0:
            raise ConfigError(f"dataset_row_count: 'min_rows' must be >= 0, got {min_rows!r}")
        if max_rows is not None and max_rows < 0:
            raise ConfigError(f"dataset_row_count: 'max_rows' must be >= 0, got {max_rows!r}")
        if min_rows is not None and max_rows is not None and min_rows > max_rows:
            raise ConfigError(
                f"dataset_row_count: 'min_rows' ({min_rows}) > 'max_rows' "
                f"({max_rows}); range can never be in-band"
            )
        self._connection_id = connection_id
        self._table = table
        self._min_rows = min_rows
        self._max_rows = max_rows
        self._where = where or None  # normalise empty string to None

    async def check_async(self) -> SensorResult:
        """Look up Connection → resolve secrets → COUNT → compare to bounds."""
        factory = sensor_session_factory.get()
        ws_id = sensor_workspace_id.get()
        backend = sensor_secret_backend.get()
        if factory is None or ws_id is None or backend is None:
            return SensorResult(
                triggered=False,
                message="dataset_row_count needs server context (DB + SecretBackend)",
                metadata={
                    "connection_id": str(self._connection_id),
                    "error": "missing_context",
                },
            )

        # 1) Connection lookup, workspace-scoped.
        async with factory() as session:
            stmt = select(Connection).where(
                Connection.id == self._connection_id,
                Connection.workspace_id == ws_id,
            )
            connection: Connection | None = (await session.execute(stmt)).scalar_one_or_none()

        if connection is None:
            return SensorResult(
                triggered=False,
                message=(
                    f"dataset_row_count: connection {self._connection_id} " "not found in workspace"
                ),
                metadata={
                    "connection_id": str(self._connection_id),
                    "error": "connection_not_found",
                },
            )

        # 2) Resolve secret placeholders + materialise the connector.
        try:
            resolved = resolve_placeholders(connection.config_json or {}, backend)
        except SecretError as e:
            return SensorResult(
                triggered=False,
                message=f"dataset_row_count: secret resolution failed: {e}",
                metadata={
                    "connection_id": str(self._connection_id),
                    "error": "secret_resolution_failed",
                    "error_class": type(e).__name__,
                },
            )
        if not isinstance(resolved, dict):
            return SensorResult(
                triggered=False,
                message="dataset_row_count: resolved config is not a JSON object",
                metadata={
                    "connection_id": str(self._connection_id),
                    "error": "invalid_config",
                },
            )
        try:
            conn_cfg = ConnectionConfig.model_validate({"type": connection.type, **resolved})
            connector = build_connector(connection.name, conn_cfg)
        except (ConfigError, Exception) as e:  # build error: connector class not registered, etc.
            return SensorResult(
                triggered=False,
                message=(
                    f"dataset_row_count: failed to build connector for " f"{connection.name!r}: {e}"
                ),
                metadata={
                    "connection_id": str(self._connection_id),
                    "connection_type": connection.type,
                    "error": "connector_build_failed",
                    "error_class": type(e).__name__,
                },
            )
        if not isinstance(connector, BatchSource):
            return SensorResult(
                triggered=False,
                message=(
                    f"dataset_row_count: connection {connection.name!r} "
                    f"(type {connection.type!r}) is not a SQL source; "
                    f"need a BatchSource that supports SELECT queries"
                ),
                metadata={
                    "connection_id": str(self._connection_id),
                    "connection_type": connection.type,
                    "error": "wrong_connection_type",
                },
            )

        # 3) Run the COUNT query off the event loop. ``read()`` returns
        # an iterator of Records; for a SELECT COUNT(*) AS n we expect
        # exactly one Record with one column "n".
        sql = _build_count_sql(self._table, self._where)
        try:
            count = await asyncio.to_thread(_run_count_query, connector, sql)
        except Exception as e:
            return SensorResult(
                triggered=False,
                message=f"dataset_row_count: COUNT query raised {type(e).__name__}",
                metadata={
                    "connection_id": str(self._connection_id),
                    "connection_type": connection.type,
                    "table": self._table,
                    "where": self._where,
                    "error": "query_failed",
                    "error_class": type(e).__name__,
                },
            )

        # 4) Compare to bounds. Either-side trigger; metadata names the
        # reason so the UI panel reads cleanly without the operator
        # having to compute the bound themselves.
        below = self._min_rows is not None and count < self._min_rows
        above = self._max_rows is not None and count > self._max_rows
        triggered = below or above
        if triggered:
            if below and above:
                reason = "below_min_and_above_max"  # logically impossible w/ scalar count
            elif below:
                reason = "below_min"
            else:
                reason = "above_max"
        else:
            reason = "in_band"
        message = _format_message(
            count=count,
            min_rows=self._min_rows,
            max_rows=self._max_rows,
            triggered=triggered,
        )
        return SensorResult(
            triggered=triggered,
            message=message,
            metadata={
                "connection_id": str(self._connection_id),
                "table": self._table,
                "where": self._where,
                "min_rows": self._min_rows,
                "max_rows": self._max_rows,
                "count": count,
                "reason": reason,
            },
        )


def _build_count_sql(table: str, where: str | None) -> str:
    """Compose the COUNT query. Trusted-input formatting — same shape
    as a pipeline's source query, no parameter binding."""
    sql = f"SELECT COUNT(*) AS n FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return sql


def _run_count_query(connector: BatchSource, sql: str) -> int:
    """Connect → iterate `read()` → take the first row's count → close.

    Connector lifecycle is owned here (sensors are stateless per
    check). ``read()`` is a generator; consume exactly one row and
    stop. Close in finally so a query error doesn't leak a pooled
    DB connection."""
    connector.connect()
    try:
        for record in connector.read(query=sql):
            # The COUNT alias ``n`` is the contract from
            # _build_count_sql. Some drivers normalise column names
            # (Postgres lowercases, MySQL preserves) — read both.
            data = record.data
            if "n" in data:
                return int(data["n"])
            if "N" in data:
                return int(data["N"])
            # Fallback: take the single value present.
            if len(data) == 1:
                return int(next(iter(data.values())))
            raise RuntimeError(f"dataset_row_count: unexpected COUNT result shape: {list(data)}")
        # Empty result (shouldn't happen for COUNT — even empty tables
        # return one row of 0). Treat as 0 so the sensor still has a
        # sensible value to compare.
        return 0
    finally:
        # Best-effort close — leak the pool slot rather than crash if
        # the driver throws during teardown (already-released cursor,
        # half-closed socket).
        with contextlib.suppress(Exception):
            connector.close()


def _format_message(
    *,
    count: int,
    min_rows: int | None,
    max_rows: int | None,
    triggered: bool,
) -> str:
    bounds_parts: list[str] = []
    if min_rows is not None:
        bounds_parts.append(f"min={min_rows}")
    if max_rows is not None:
        bounds_parts.append(f"max={max_rows}")
    bounds = ", ".join(bounds_parts)
    if triggered:
        return f"row count {count} out of band ({bounds})"
    return f"row count {count} in band ({bounds})"


@register_sensor("dataset_row_count")
def _build(config: Mapping[str, Any]) -> SensorBase:
    """Builder for :class:`DatasetRowCountSensor`.

    Raises :class:`ConfigError` on missing / wrong-typed config so a
    POST /sensors with a typo returns a clear 4xx instead of a generic
    500."""
    raw_conn = config.get("connection_id")
    if not isinstance(raw_conn, str) or not raw_conn:
        raise ConfigError("dataset_row_count: 'connection_id' must be a non-empty UUID string")
    try:
        conn_uuid = UUID(raw_conn)
    except ValueError as e:
        raise ConfigError(
            f"dataset_row_count: 'connection_id' is not a valid UUID: {raw_conn!r}"
        ) from e

    raw_table = config.get("table")
    if not isinstance(raw_table, str):
        raise ConfigError("dataset_row_count: 'table' must be a string")

    def _coerce_int(key: str) -> int | None:
        v = config.get(key)
        if v is None:
            return None
        # Reject booleans explicitly — ``isinstance(True, int)`` is True
        # so a JSON ``"min_rows": true`` would otherwise slip through
        # as ``1``. Same trap we caught in the other sensor builders.
        if not isinstance(v, int) or isinstance(v, bool):
            raise ConfigError(f"dataset_row_count: {key!r} must be an integer or null")
        return v

    raw_where = config.get("where")
    if raw_where is not None and not isinstance(raw_where, str):
        raise ConfigError("dataset_row_count: 'where' must be a string or null")

    return DatasetRowCountSensor(
        connection_id=conn_uuid,
        table=raw_table,
        min_rows=_coerce_int("min_rows"),
        max_rows=_coerce_int("max_rows"),
        where=raw_where,
    )


__all__ = ["DatasetRowCountSensor"]
