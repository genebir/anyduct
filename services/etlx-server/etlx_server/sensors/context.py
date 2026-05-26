"""Per-check ContextVars exposing service-side dependencies to async sensors.

The core :class:`etl_plugins.core.sensor.SensorBase` API is deliberately
orchestrator-agnostic — its builders only see the user's ``config_json``,
not the metadata DB session or the workspace the sensor row belongs to.
That's the right choice for pure-network sensors (HTTP), but it doesn't
work for sensors that *need* server-internal state — e.g. the
:class:`AssetFreshnessSensor` reads ``assets.last_materialized_at`` from
the metadata DB and must scope by ``workspace_id``.

Rather than break the core builder signature, the service layer injects
those dependencies via :class:`contextvars.ContextVar` set immediately
before ``check_async`` and reset right after. ContextVar inherits across
``await`` and ``asyncio.to_thread`` boundaries (per PEP 567), so both
async-overriding subclasses (asset-freshness) and sync-bridging
subclasses (HTTP) see the same context. Same shape as the run-id var
used by :mod:`etlx_server.worker.recorder`.

Helpers:
    * :data:`sensor_session_factory` — the ``async_sessionmaker`` the
      scheduler / REST endpoint is using. ``None`` outside a sensor
      check (so unrelated code reading the var sees the unset sentinel
      instead of a stray live factory).
    * :data:`sensor_workspace_id` — UUID of the sensor row's workspace.
    * :func:`use_sensor_context` — async context manager that sets both
      vars + resets on exit even if the check raises. Both the
      scheduler and the manual-check REST endpoint route through this
      so the contract is in one place.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from contextvars import ContextVar
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ``None`` is the unset sentinel — service-side sensors that need DB access
# must defend against it and return a soft-fail ``SensorResult`` rather
# than crash, so a user instantiating the sensor outside the scheduler
# (e.g. in a unit test that forgot to set context) gets a clear message.
sensor_session_factory: ContextVar[async_sessionmaker[AsyncSession] | None] = ContextVar(
    "sensor_session_factory", default=None
)
sensor_workspace_id: ContextVar[UUID | None] = ContextVar("sensor_workspace_id", default=None)


@contextlib.asynccontextmanager
async def use_sensor_context(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    workspace_id: UUID,
) -> AsyncIterator[None]:
    """Bind the per-check ContextVars for the duration of ``async with``.

    Both vars are reset on exit (success *or* exception) so the next
    check sees a clean slate. Using ContextVar.set's token-based reset
    rather than ``ContextVar.set(None)`` keeps the original outer value
    intact if anything above us also bound the vars (the scheduler
    doesn't, but tests sometimes do)."""
    sf_token = sensor_session_factory.set(session_factory)
    ws_token = sensor_workspace_id.set(workspace_id)
    try:
        yield
    finally:
        sensor_session_factory.reset(sf_token)
        sensor_workspace_id.reset(ws_token)


__all__ = [
    "sensor_session_factory",
    "sensor_workspace_id",
    "use_sensor_context",
]
