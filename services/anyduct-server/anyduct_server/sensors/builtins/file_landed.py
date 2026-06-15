"""``file_landed`` sensor — fires when an object lands in S3-compatible storage.

The classic Airflow ``S3KeySensor`` shape, ported to our framework. A
consumer pipeline declares the bucket/prefix it watches (via a workspace
:class:`Connection`) and an optional filename pattern; when one or more
matching object arrives the sensor fires and the scheduler enqueues the
target pipeline. With :class:`AssetFreshnessSensor` (asset-axis pull)
and :class:`LineageArrivalSensor` (asset-axis push) this rounds out the
asset-axis triggers with **external-axis** ones — events that originate
*outside* our catalog.

Config (``config_json``):
    connection_id: str (UUID)
        References a workspace ``Connection`` of type ``s3``. Carries
        the bucket creds + optional ``endpoint_url`` (MinIO etc.). The
        sensor resolves any ``${SECRET:<path>}`` placeholders the same
        way the worker does, so the operator never has to embed
        credentials in the sensor config itself.
    prefix: str
        ``list_objects_v2`` key prefix (e.g. ``"incoming/orders/"``).
        An empty string lists the whole bucket — allowed but rarely
        intended; usually you want at least a folder.
    pattern: str (default ``"*"``)
        Glob applied to the *trailing* path segment (basename). E.g.
        ``"*.parquet"`` lets you watch a partitioned prefix but only
        fire on the final file format. Uses :mod:`fnmatch` semantics.
    min_size_bytes: int (default 0)
        Skip objects strictly smaller than this. Defaults to 0 so a
        zero-byte placeholder still triggers; raise it to e.g. 1 to
        require non-empty payloads.

De-duplication: uses the sensor row's ``last_triggered_at`` (via
:data:`anyduct_server.sensors.context.sensor_last_triggered_at`) as a
``LastModified > since`` cutoff so the same file landing doesn't keep
firing on every subsequent tick. ``None`` (sensor never fired) means
"any matching object triggers".

Soft-fail policy (no scheduler crash on a flaky upstream):
    * Connection not found / wrong type → triggered=False with metadata
      pointing at the misconfiguration.
    * Secret resolution failure        → triggered=False with reason.
    * S3 call raises                   → triggered=False with the error
      class + bucket (no creds).
Hard config errors (missing keys at build time) raise :class:`ConfigError`
so the row never persists in an unrunnable state.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select

from anyduct_server.db.models import Connection
from anyduct_server.pipelines.runtime import resolve_placeholders
from anyduct_server.sensors.context import (
    sensor_last_triggered_at,
    sensor_secret_backend,
    sensor_session_factory,
    sensor_workspace_id,
)
from etl_plugins.core.exceptions import ConfigError, SecretError
from etl_plugins.core.sensor import SensorBase, SensorResult, register_sensor


class FileLandedSensor(SensorBase):
    """Polls an S3 prefix; fires when one or more matching objects arrive."""

    def __init__(
        self,
        *,
        connection_id: UUID,
        prefix: str,
        pattern: str = "*",
        min_size_bytes: int = 0,
    ) -> None:
        # Validate eagerly at config time so a typo (missing connection,
        # negative size threshold) surfaces as a 4xx on POST /sensors
        # instead of a quiet "never fires" sensor row.
        if not pattern:
            raise ConfigError("file_landed: 'pattern' must be a non-empty string")
        if min_size_bytes < 0:
            raise ConfigError(f"file_landed: 'min_size_bytes' must be >= 0, got {min_size_bytes!r}")
        self._connection_id = connection_id
        # Empty prefix listing the whole bucket is uncommon but legal.
        self._prefix = prefix or ""
        self._pattern = pattern
        self._min_size_bytes = int(min_size_bytes)

    async def check_async(self) -> SensorResult:
        """Resolve the linked Connection, list objects under ``prefix``,
        keep those matching ``pattern`` + size + cutoff, trigger if any."""
        factory = sensor_session_factory.get()
        ws_id = sensor_workspace_id.get()
        last_triggered_at = sensor_last_triggered_at.get()
        backend = sensor_secret_backend.get()
        if factory is None or ws_id is None or backend is None:
            return SensorResult(
                triggered=False,
                message="file_landed needs server context (DB + SecretBackend) — call via SensorScheduler",
                metadata={
                    "connection_id": str(self._connection_id),
                    "error": "missing_context",
                },
            )

        # 1) Look up + validate the linked Connection inside the same
        # workspace. Cross-workspace access is impossible because the
        # WHERE clause pins workspace_id, so a tenant can't trick us
        # into reading another tenant's S3 creds even if they UUID-guess.
        async with factory() as session:
            stmt = select(Connection).where(
                Connection.id == self._connection_id,
                Connection.workspace_id == ws_id,
            )
            connection: Connection | None = (await session.execute(stmt)).scalar_one_or_none()

        if connection is None:
            return SensorResult(
                triggered=False,
                message=(f"file_landed: connection {self._connection_id} not found in workspace"),
                metadata={
                    "connection_id": str(self._connection_id),
                    "error": "connection_not_found",
                },
            )
        if connection.type != "s3":
            return SensorResult(
                triggered=False,
                message=(
                    f"file_landed: connection {connection.name!r} is type "
                    f"{connection.type!r}, expected 's3'"
                ),
                metadata={
                    "connection_id": str(self._connection_id),
                    "connection_type": connection.type,
                    "error": "wrong_connection_type",
                },
            )

        # 2) Resolve secret placeholders. The walker stored
        # ``${SECRET:<path>}`` strings at create time; the worker /
        # tester / dry-runner use the same helper, so behavior is
        # consistent across every place a connection is materialised.
        try:
            resolved_config = resolve_placeholders(connection.config_json or {}, backend)
        except SecretError as e:
            return SensorResult(
                triggered=False,
                message=f"file_landed: secret resolution failed: {e}",
                metadata={
                    "connection_id": str(self._connection_id),
                    "error": "secret_resolution_failed",
                    "error_class": type(e).__name__,
                },
            )

        bucket = resolved_config.get("bucket") if isinstance(resolved_config, dict) else None
        if not bucket:
            return SensorResult(
                triggered=False,
                message=(f"file_landed: connection {connection.name!r} has no 'bucket' configured"),
                metadata={
                    "connection_id": str(self._connection_id),
                    "error": "no_bucket",
                },
            )

        # 3) Build the S3 client via the same connector the worker uses,
        # then call list_objects_v2 off the event loop so a slow bucket
        # doesn't pin the scheduler. Keep close() in a finally so a
        # mid-flight error doesn't leak the boto3 session pool.
        client = _build_s3_client(resolved_config)

        now = datetime.now(UTC)
        # ``last_triggered_at`` is the cutoff for de-dupe; use ``None`` →
        # "any matching object qualifies" (typical first-fire bootstrap).
        cutoff = last_triggered_at
        if cutoff is not None and cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)

        try:
            objects = await _list_matching_objects(
                client,
                bucket=str(bucket),
                prefix=self._prefix,
                pattern=self._pattern,
                min_size_bytes=self._min_size_bytes,
                cutoff=cutoff,
            )
        except Exception as e:
            # Soft-fail per contract — never crash the scheduler on a
            # transient S3 hiccup. Strip secrets from the message; the
            # error_class is enough for an operator to recognise auth /
            # network / not-found issues.
            return SensorResult(
                triggered=False,
                message=f"file_landed: S3 list_objects_v2 raised {type(e).__name__}",
                metadata={
                    "connection_id": str(self._connection_id),
                    "bucket": bucket,
                    "prefix": self._prefix,
                    "error": "s3_call_failed",
                    "error_class": type(e).__name__,
                },
            )

        triggered = len(objects) > 0
        message = (
            f"matched {len(objects)} object(s) under {bucket}/{self._prefix} "
            f"matching {self._pattern!r}"
            if triggered
            else f"no matching object under {bucket}/{self._prefix}"
        )
        return SensorResult(
            triggered=triggered,
            message=message,
            metadata={
                "connection_id": str(self._connection_id),
                "bucket": bucket,
                "prefix": self._prefix,
                "pattern": self._pattern,
                "min_size_bytes": self._min_size_bytes,
                "cutoff": cutoff.isoformat() if cutoff is not None else None,
                "now": now.isoformat(),
                "matched": objects[:50],  # cap so a 10k-object bucket doesn't bloat the row
                "match_count": len(objects),
                "match_count_truncated": len(objects) > 50,
            },
        )


def _build_s3_client(resolved_config: Mapping[str, Any]) -> Any:
    """Build an S3 client by routing through :class:`S3Connector`.

    Reuses the connector's exact boto3 setup (region, endpoint_url, creds,
    extra kwargs) so the sensor sees the same S3 surface the worker
    would. Returns the underlying boto3 client; the connector instance
    itself is discarded — we don't need its read/write methods.

    Factored out as a module-level helper so tests can monkeypatch it
    to return a fake client with stub ``list_objects_v2`` instead of
    standing up LocalStack just for one sensor."""
    from etl_plugins.connectors.object_storage.s3 import S3Connector

    # Strip Connection-side fields the connector doesn't accept; pass the
    # rest through so future S3Connector kwargs land here automatically.
    kwargs = {k: v for k, v in resolved_config.items() if k not in {"type", "name"}}
    connector = S3Connector(**kwargs)
    connector.connect()
    return connector.client


async def _list_matching_objects(
    client: Any,
    *,
    bucket: str,
    prefix: str,
    pattern: str,
    min_size_bytes: int,
    cutoff: datetime | None,
) -> list[dict[str, Any]]:
    """Paginate ``list_objects_v2`` off the event loop, return matches.

    Match = basename matches ``pattern`` (fnmatch) AND ``Size >=
    min_size_bytes`` AND (``cutoff is None`` OR ``LastModified > cutoff``).
    Returns a list of ``{key, size, last_modified}`` dicts in pagination
    order — useful as result_json metadata.
    """
    import asyncio

    def _scan() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                key = obj.get("Key", "")
                basename = key.rsplit("/", 1)[-1]
                if not fnmatch.fnmatch(basename, pattern):
                    continue
                size = int(obj.get("Size", 0))
                if size < min_size_bytes:
                    continue
                last_mod = obj.get("LastModified")
                if isinstance(last_mod, datetime) and last_mod.tzinfo is None:
                    last_mod = last_mod.replace(tzinfo=UTC)
                if cutoff is not None and last_mod is not None and last_mod <= cutoff:
                    continue
                out.append(
                    {
                        "key": key,
                        "size": size,
                        "last_modified": last_mod.isoformat()
                        if isinstance(last_mod, datetime)
                        else None,
                    }
                )
        return out

    return await asyncio.to_thread(_scan)


# Reference suppression so unused-import linting doesn't fire on
# timedelta — kept available for ConfigError messages that might want
# duration math in future tweaks (e.g. window cap like lineage_arrival).
_ = timedelta


@register_sensor("file_landed")
def _build(config: Mapping[str, Any]) -> SensorBase:
    """Builder for :class:`FileLandedSensor`.

    Surfaced via :func:`etl_plugins.core.sensor.build_sensor`. Raises
    :class:`ConfigError` on missing / wrong-typed config keys so the
    REST + scheduler error paths render a clear 4xx instead of a
    generic 500."""
    raw_conn = config.get("connection_id")
    if not isinstance(raw_conn, str) or not raw_conn:
        raise ConfigError("file_landed: 'connection_id' must be a non-empty UUID string")
    try:
        conn_uuid = UUID(raw_conn)
    except ValueError as e:
        raise ConfigError(f"file_landed: 'connection_id' is not a valid UUID: {raw_conn!r}") from e

    raw_prefix = config.get("prefix")
    if not isinstance(raw_prefix, str):
        raise ConfigError("file_landed: 'prefix' must be a string (use \"\" to list whole bucket)")

    raw_pattern = config.get("pattern", "*")
    if not isinstance(raw_pattern, str):
        raise ConfigError("file_landed: 'pattern' must be a string (default '*')")

    raw_min_size = config.get("min_size_bytes", 0)
    if not isinstance(raw_min_size, int) or isinstance(raw_min_size, bool):
        # isinstance(True, int) trap — reject bools explicitly.
        raise ConfigError("file_landed: 'min_size_bytes' must be a non-negative integer")

    return FileLandedSensor(
        connection_id=conn_uuid,
        prefix=raw_prefix,
        pattern=raw_pattern,
        min_size_bytes=raw_min_size,
    )


__all__ = ["FileLandedSensor"]
