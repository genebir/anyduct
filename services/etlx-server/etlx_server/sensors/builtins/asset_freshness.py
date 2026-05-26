"""``asset_freshness`` sensor — fires when a target asset goes stale.

Reads the workspace-scoped catalog (``assets`` table, populated by the
worker each time a Run completes) and asks: "is the named asset newer
than ``max_age_minutes``?". Anything older — or an asset that's never
materialised — triggers a Run of the sensor's target pipeline.

The asset-axis dual of the pipeline-axis ``freshness_sla_minutes`` field
on :class:`Schedule` (ADR-0038): both watch "is data still fresh enough?"
but a sensor lets one workspace declare it from the *consumer* side
without having to add SLA configuration to every producer pipeline. The
sensor needs DB access — see :mod:`etlx_server.sensors.context` for the
ContextVar protocol it relies on.

Config (``config_json``):
    asset_key: str        — the ``assets.asset_key`` to watch (e.g.
                            ``postgres://prod/main/users``). Sensors are
                            workspace-scoped at the row level, so we
                            don't take a workspace id here.
    max_age_minutes: int  — fire when ``now - last_materialized_at >
                            max_age_minutes`` (or when the asset has
                            never materialised).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from etl_plugins.core.exceptions import ConfigError
from etl_plugins.core.sensor import SensorBase, SensorResult, register_sensor
from etlx_server.db.models import Asset
from etlx_server.sensors.context import sensor_session_factory, sensor_workspace_id


class AssetFreshnessSensor(SensorBase):
    """Checks a single asset's ``last_materialized_at`` against an age budget."""

    def __init__(self, *, asset_key: str, max_age_minutes: int) -> None:
        # Validate at build time, not at every check, so a typo in the YAML/UI
        # surfaces as a clear 4xx on POST /sensors instead of a quiet
        # never-triggering sensor row.
        if not asset_key:
            raise ConfigError("asset_freshness: 'asset_key' is required and must be non-empty")
        if max_age_minutes <= 0:
            raise ConfigError(
                f"asset_freshness: 'max_age_minutes' must be positive, got {max_age_minutes!r}"
            )
        self._asset_key = asset_key
        self._max_age_minutes = int(max_age_minutes)

    async def check_async(self) -> SensorResult:
        """Look up the asset, compare ``last_materialized_at`` to the budget.

        Soft-fails (``triggered=False``) on missing context or a missing
        asset row, with descriptive messages so the operator can debug
        the result from the UI panel without re-running. The "never
        materialised" branch *does* trigger — that's the intended way to
        bootstrap downstream pipelines on a fresh workspace."""
        factory = sensor_session_factory.get()
        ws_id = sensor_workspace_id.get()
        if factory is None or ws_id is None:
            # Hit only if someone calls ``check_async`` outside
            # ``use_sensor_context`` (the scheduler / REST always set it).
            # Surfacing this as a soft-fail with a precise message turns
            # a confused crash into a discoverable bug.
            return SensorResult(
                triggered=False,
                message="asset_freshness needs server context — call via SensorScheduler",
                metadata={"asset_key": self._asset_key, "error": "missing_context"},
            )

        async with factory() as session:
            stmt = select(Asset.last_materialized_at).where(
                Asset.workspace_id == ws_id,
                Asset.asset_key == self._asset_key,
            )
            last_materialized_at: datetime | None = (
                await session.execute(stmt)
            ).scalar_one_or_none()

        now = datetime.now(UTC)

        if last_materialized_at is None:
            # Asset key not yet in the catalog — either it's never run, or
            # the operator typo'd the key. Trigger anyway so the consumer
            # pipeline does its first materialisation; the metadata
            # records "never" so a misconfigured sensor is debuggable.
            return SensorResult(
                triggered=True,
                message=f"asset {self._asset_key!r} has never materialised",
                metadata={
                    "asset_key": self._asset_key,
                    "reason": "never_materialised",
                    "max_age_minutes": self._max_age_minutes,
                },
            )

        # DB columns stored as TIMESTAMPTZ come back tz-aware, but defend
        # against legacy naive values landing here from older migrations.
        if last_materialized_at.tzinfo is None:
            last_materialized_at = last_materialized_at.replace(tzinfo=UTC)

        age_minutes = (now - last_materialized_at).total_seconds() / 60.0
        age_minutes = round(age_minutes, 2)

        if age_minutes > self._max_age_minutes:
            return SensorResult(
                triggered=True,
                message=(
                    f"asset {self._asset_key!r} is stale: "
                    f"age {age_minutes:.1f}m > budget {self._max_age_minutes}m"
                ),
                metadata={
                    "asset_key": self._asset_key,
                    "reason": "stale",
                    "age_minutes": age_minutes,
                    "max_age_minutes": self._max_age_minutes,
                    "last_materialized_at": last_materialized_at.isoformat(),
                },
            )
        return SensorResult(
            triggered=False,
            message=(
                f"asset {self._asset_key!r} fresh: "
                f"age {age_minutes:.1f}m ≤ budget {self._max_age_minutes}m"
            ),
            metadata={
                "asset_key": self._asset_key,
                "reason": "fresh",
                "age_minutes": age_minutes,
                "max_age_minutes": self._max_age_minutes,
                "last_materialized_at": last_materialized_at.isoformat(),
            },
        )


@register_sensor("asset_freshness")
def _build(config: Mapping[str, Any]) -> SensorBase:
    """Builder for :class:`AssetFreshnessSensor`.

    Surfaced as :func:`etl_plugins.core.sensor.build_sensor` dispatches
    on the sensor row's ``type`` column. Raises :class:`ConfigError` on
    missing / wrong-typed config keys so the REST + scheduler error
    paths render a clear 4xx (rather than a generic 500)."""
    asset_key = config.get("asset_key")
    if not isinstance(asset_key, str):
        raise ConfigError("asset_freshness: 'asset_key' must be a string")
    raw_max_age = config.get("max_age_minutes")
    if not isinstance(raw_max_age, int) or isinstance(raw_max_age, bool):
        # ``isinstance(True, int)`` is True — explicitly reject booleans so a
        # typo like ``"max_age_minutes": true`` doesn't slip through as 1.
        raise ConfigError("asset_freshness: 'max_age_minutes' must be an integer")
    return AssetFreshnessSensor(asset_key=asset_key, max_age_minutes=raw_max_age)


__all__ = ["AssetFreshnessSensor"]
