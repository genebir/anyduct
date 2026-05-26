"""Sensor framework — external-event triggers (ADR-0041 K3).

A *sensor* is a small, deterministic check that runs on a schedule and decides
whether an external condition is now true (file has landed, HTTP endpoint
returns a success body, an asset is stale, …). When the check returns
``triggered=True`` the orchestration layer enqueues a Run of the sensor's
configured target pipeline — the Airflow-style "wait for the world to be
ready, then go" pattern.

This module is the **core only**: an ABC + result dataclass + registry. It
deliberately knows nothing about persistence or scheduling — those land in
the service layer (K3b) so the core stays runtime-agnostic and can be unit
tested without spinning up Postgres.

Pattern mirrors :mod:`etl_plugins.runtime.transforms` and
:mod:`etl_plugins.core.registry`: a global ``_REGISTRY`` populated by
``@register_sensor("name")``, and a :func:`build_sensor` dispatcher that the
service tick loop calls. External packages can ship their own sensor types
by importing this module and decorating their class.

Concrete sensors built in:
    * ``http``  — :class:`HttpSensor`. Polls a URL; triggers when the response
      matches a status-code + optional substring condition.
    * (more land alongside K3b once the persistence layer is ready —
      file-landed via the object-storage connectors, asset-freshness using
      the existing catalog, time-based using cron.)

Sensors are **idempotent by design**: ``check()`` must be safe to call many
times with no side effects. The scheduler decides "was the last result
triggered?" by reading the persisted ``last_result_json``; the sensor body
just answers the question.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SensorResult:
    """One sensor evaluation outcome.

    ``triggered`` — the external condition is now true; the scheduler should
    enqueue a Run of the sensor's target pipeline. ``False`` ⇒ keep polling.

    ``message`` — short human-readable explanation (logged + persisted to
    ``last_result_json`` for UI display). Optional but strongly encouraged
    so an operator can debug a quiet sensor without re-running it.

    ``metadata`` — arbitrary JSON-serialisable extras (response status,
    matched value, latency, …). The scheduler stamps it onto the triggered
    Run's ``result_json`` so downstream pipelines can read what fired them.
    """

    triggered: bool
    message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class SensorBase(ABC):  # noqa: B024 — "abstract" is one-of-two: check OR check_async
    """A pollable check. Implementations are built once at config time and
    reused across many ``check()`` calls.

    Subclasses override **either** :meth:`check` (sync — for plain HTTP / file
    polling) **or** :meth:`check_async` (for sensors that need an async DB
    session or async client). The scheduler always calls ``check_async`` and
    the base implementation transparently bridges sync ``check`` via
    ``asyncio.to_thread`` so existing sync builtins keep working without
    change. A subclass that overrides only ``check_async`` should leave the
    default ``check`` body (raises NotImplementedError) — the scheduler will
    never reach it."""

    def check(self) -> SensorResult:
        """Sync check. Override for sensors with no async I/O needs.
        Must be idempotent. Should never raise on a "soft" failure (network
        timeout, 5xx, missing file) — return ``triggered=False`` with a
        descriptive ``message`` instead so the scheduler can log + retry on
        the next tick. Hard errors (misconfigured sensor) may raise.

        Default raises ``NotImplementedError`` — subclasses must override
        this OR :meth:`check_async`."""
        raise NotImplementedError(f"{type(self).__name__} must implement check() or check_async()")

    async def check_async(self) -> SensorResult:
        """Async check entry point — what the scheduler / REST manual-check
        endpoint actually calls. Default bridges to :meth:`check` via
        ``asyncio.to_thread`` so a CPU/blocking-IO sync check doesn't pin the
        event loop. Override directly when the sensor needs an async client
        (e.g. an :class:`AsyncSession` from SQLAlchemy)."""
        return await asyncio.to_thread(self.check)


# ---- registry ----------------------------------------------------------------

# A sensor builder takes the config dict and returns an instance. Mirrors the
# transform / connector pattern so external packages plug in identically.
SensorBuilder = Callable[[Mapping[str, Any]], SensorBase]

_REGISTRY: dict[str, SensorBuilder] = {}


def register_sensor(name: str) -> Callable[[SensorBuilder], SensorBuilder]:
    """Register a sensor builder under ``name`` (raises on duplicate)."""

    def deco(builder: SensorBuilder) -> SensorBuilder:
        if name in _REGISTRY:
            from etl_plugins.core.exceptions import ConfigError

            raise ConfigError(f"sensor type {name!r} is already registered")
        _REGISTRY[name] = builder
        return builder

    return deco


def build_sensor(sensor_type: str, config: Mapping[str, Any]) -> SensorBase:
    """Resolve a sensor builder by ``sensor_type`` and apply it to ``config``."""
    from etl_plugins.core.exceptions import ConfigError

    builder = _REGISTRY.get(sensor_type)
    if builder is None:
        raise ConfigError(f"unknown sensor type: {sensor_type!r} (registered: {sorted(_REGISTRY)})")
    return builder(config)


def registered_sensor_types() -> list[str]:
    """Return the names of all registered sensors (for catalog endpoints)."""
    return sorted(_REGISTRY)


__all__ = [
    "SensorBase",
    "SensorBuilder",
    "SensorResult",
    "build_sensor",
    "register_sensor",
    "registered_sensor_types",
]
