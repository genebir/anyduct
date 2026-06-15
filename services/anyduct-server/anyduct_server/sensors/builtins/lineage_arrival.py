"""``lineage_arrival`` sensor — fires when upstream assets materialise.

Asset-axis sensor for event-driven DAG composition: a consumer pipeline
declares the asset_keys it depends on, and runs whenever those upstreams
land fresh data. Removes the need to schedule consumers separately from
producers — the lineage axis is what triggers the next step. Pairs with
the existing :class:`AssetFreshnessSensor` (asset-axis dual of the
pipeline-axis ``Schedule.freshness_sla_minutes``): freshness asks "is
this asset stale right now?", arrival asks "did upstream just deliver?".

Config (``config_json``):
    upstream_asset_keys: list[str]
        Asset keys to watch (e.g. ``["postgres://prod/main/orders",
        "postgres://prod/main/users"]``). Must be non-empty.
    window_minutes: int
        Only consider materialisations newer than ``now - window_minutes``.
        Caps how far back we look so a long-dead upstream doesn't keep
        firing the sensor forever; pair with ``require_all=false`` to
        get "fire whenever ANY upstream is recent" semantics. Must be
        positive.
    require_all: bool (default true)
        ``true``  — fire only when every upstream has a fresh
                    materialisation (graph "join-then-run").
        ``false`` — fire when ANY upstream has a fresh materialisation
                    (graph "fan-out trigger").

De-duplication: this sensor reads its own row's ``last_triggered_at``
via :data:`anyduct_server.sensors.context.sensor_last_triggered_at` and
uses ``max(window_threshold, last_triggered_at)`` as the materialisation
cutoff. Same materialisation event therefore can never fire the sensor
twice — once it fires at T, subsequent ticks only consider events newer
than T even if the window would otherwise still include the original
event.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from anyduct_server.db.models import Asset
from anyduct_server.sensors.context import (
    sensor_last_triggered_at,
    sensor_session_factory,
    sensor_workspace_id,
)
from etl_plugins.core.exceptions import ConfigError
from etl_plugins.core.sensor import SensorBase, SensorResult, register_sensor


class LineageArrivalSensor(SensorBase):
    """Watches one or more upstream asset keys; fires on new materialisations."""

    def __init__(
        self,
        *,
        upstream_asset_keys: list[str],
        window_minutes: int,
        require_all: bool = True,
    ) -> None:
        # Validate eagerly so a misconfigured sensor surfaces as a 4xx on
        # POST /sensors, not a quiet "never fires" row in production.
        if not upstream_asset_keys:
            raise ConfigError(
                "lineage_arrival: 'upstream_asset_keys' is required and must be non-empty"
            )
        for key in upstream_asset_keys:
            if not isinstance(key, str) or not key:
                raise ConfigError(
                    f"lineage_arrival: every upstream_asset_keys entry must be a non-empty string, got {key!r}"
                )
        if window_minutes <= 0:
            raise ConfigError(
                f"lineage_arrival: 'window_minutes' must be positive, got {window_minutes!r}"
            )
        # Deduplicate while preserving order — a user who pastes the
        # same key twice shouldn't change the "all arrived" semantics.
        seen: set[str] = set()
        deduped: list[str] = []
        for k in upstream_asset_keys:
            if k not in seen:
                seen.add(k)
                deduped.append(k)
        self._upstream_asset_keys = deduped
        self._window_minutes = int(window_minutes)
        self._require_all = bool(require_all)

    async def check_async(self) -> SensorResult:
        """Look up each upstream's ``last_materialized_at`` in the catalog,
        compare against ``since = max(window_threshold, last_triggered_at)``,
        then evaluate ``require_all`` to decide trigger."""
        factory = sensor_session_factory.get()
        ws_id = sensor_workspace_id.get()
        last_triggered_at = sensor_last_triggered_at.get()
        if factory is None or ws_id is None:
            return SensorResult(
                triggered=False,
                message="lineage_arrival needs server context — call via SensorScheduler",
                metadata={
                    "upstream_asset_keys": list(self._upstream_asset_keys),
                    "error": "missing_context",
                },
            )

        now = datetime.now(UTC)
        # Window cap so a long-dead upstream doesn't trigger forever.
        window_threshold = now - timedelta(minutes=self._window_minutes)
        # Dedupe cap so a single materialisation event doesn't fire the
        # sensor on every tick. None means "never fired yet" → only the
        # window applies.
        if last_triggered_at is None:
            since = window_threshold
        else:
            if last_triggered_at.tzinfo is None:
                last_triggered_at = last_triggered_at.replace(tzinfo=UTC)
            since = max(window_threshold, last_triggered_at)

        async with factory() as session:
            stmt = select(Asset.asset_key, Asset.last_materialized_at).where(
                Asset.workspace_id == ws_id,
                Asset.asset_key.in_(self._upstream_asset_keys),
            )
            rows = list((await session.execute(stmt)).all())
        by_key: dict[str, datetime | None] = {row[0]: row[1] for row in rows}

        arrived: list[tuple[str, datetime]] = []
        stale: list[str] = []  # in catalog but no materialisation since `since`
        missing: list[str] = []  # not in catalog at all
        for key in self._upstream_asset_keys:
            mat = by_key.get(key, _MISSING)
            if mat is _MISSING:
                missing.append(key)
                continue
            if mat is None:
                # Asset row exists but never materialised — treat as stale.
                stale.append(key)
                continue
            if mat.tzinfo is None:  # legacy naive timestamps
                mat = mat.replace(tzinfo=UTC)
            if mat > since:
                arrived.append((key, mat))
            else:
                stale.append(key)

        total = len(self._upstream_asset_keys)
        if self._require_all:
            triggered = len(arrived) == total
            if triggered:
                message = f"all {total} upstream(s) arrived since {since.isoformat()}"
            else:
                message = (
                    f"only {len(arrived)}/{total} upstream(s) arrived since {since.isoformat()}"
                )
        else:
            triggered = len(arrived) > 0
            if triggered:
                message = f"{len(arrived)}/{total} upstream(s) arrived since {since.isoformat()}"
            else:
                message = f"no upstream arrival since {since.isoformat()}"

        metadata: dict[str, Any] = {
            "arrived": [{"asset_key": k, "materialized_at": v.isoformat()} for k, v in arrived],
            "stale": list(stale),
            "missing": list(missing),
            "since": since.isoformat(),
            "require_all": self._require_all,
            "window_minutes": self._window_minutes,
        }
        return SensorResult(triggered=triggered, message=message, metadata=metadata)


# Distinct sentinel because ``None`` is a *valid* value in the by_key dict
# (an asset row that exists but was never materialised) — different from
# the asset_key not being in the catalog at all.
_MISSING: Any = object()


@register_sensor("lineage_arrival")
def _build(config: Mapping[str, Any]) -> SensorBase:
    """Builder for :class:`LineageArrivalSensor`.

    Surfaced as :func:`etl_plugins.core.sensor.build_sensor` dispatches
    on the sensor row's ``type`` column. Raises :class:`ConfigError` on
    missing / wrong-typed config keys so the REST + scheduler error
    paths render a clear 4xx rather than a generic 500."""
    raw_keys = config.get("upstream_asset_keys")
    if not isinstance(raw_keys, list):
        raise ConfigError(
            "lineage_arrival: 'upstream_asset_keys' must be a list of asset_key strings"
        )
    raw_window = config.get("window_minutes")
    if not isinstance(raw_window, int) or isinstance(raw_window, bool):
        # ``isinstance(True, int)`` is True — explicitly reject bools so
        # a ``"window_minutes": true`` typo doesn't slip through as 1.
        raise ConfigError("lineage_arrival: 'window_minutes' must be an integer")
    raw_require_all = config.get("require_all", True)
    if not isinstance(raw_require_all, bool):
        raise ConfigError("lineage_arrival: 'require_all' must be a boolean (default true)")
    return LineageArrivalSensor(
        upstream_asset_keys=list(raw_keys),
        window_minutes=raw_window,
        require_all=raw_require_all,
    )


__all__ = ["LineageArrivalSensor"]
