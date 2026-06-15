"""SensorRepository — CRUD + ``last_*`` cache updates (ADR-0041 K3b).

Domain errors flow to the REST router as 4xx; everything else is the
plain SQLAlchemy session API the rest of the server uses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.db.models import Sensor
from etl_plugins.core.sensor import registered_sensor_types


class SensorNameTakenError(Exception):
    """Two sensors in the same workspace can't share a name (UNIQUE
    constraint). Surfaced as HTTP 409."""


class UnknownSensorTypeError(Exception):
    """The requested sensor ``type`` isn't registered in the core's
    :func:`etl_plugins.core.sensor.build_sensor` dispatcher. Surfaced as
    HTTP 422 with the list of valid types."""


class SensorRepository:
    """Async CRUD over the ``sensors`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---- read --------------------------------------------------------------

    async def list_for_workspace(self, *, workspace_id: UUID) -> list[Sensor]:
        result = await self._session.execute(
            select(Sensor).where(Sensor.workspace_id == workspace_id).order_by(Sensor.name)
        )
        return list(result.scalars().all())

    async def get(self, *, workspace_id: UUID, sensor_id: UUID) -> Sensor | None:
        return (
            await self._session.execute(
                select(Sensor).where(Sensor.id == sensor_id, Sensor.workspace_id == workspace_id)
            )
        ).scalar_one_or_none()

    async def get_by_name(self, *, workspace_id: UUID, name: str) -> Sensor | None:
        return (
            await self._session.execute(
                select(Sensor).where(Sensor.workspace_id == workspace_id, Sensor.name == name)
            )
        ).scalar_one_or_none()

    # ---- write -------------------------------------------------------------

    async def create(
        self,
        *,
        workspace_id: UUID,
        name: str,
        sensor_type: str,
        config_json: dict[str, Any],
        target_pipeline_id: UUID | None,
        poll_interval_seconds: int,
        is_active: bool,
        created_by_user_id: UUID | None,
    ) -> Sensor:
        if sensor_type not in registered_sensor_types():
            raise UnknownSensorTypeError(sensor_type)
        if await self.get_by_name(workspace_id=workspace_id, name=name) is not None:
            raise SensorNameTakenError(name)
        sensor = Sensor(
            workspace_id=workspace_id,
            name=name,
            type=sensor_type,
            config_json=config_json,
            target_pipeline_id=target_pipeline_id,
            poll_interval_seconds=poll_interval_seconds,
            is_active=is_active,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(sensor)
        await self._session.flush()
        # ``created_at`` / ``updated_at`` are server-default columns — pull
        # them back from the DB so the response schema can read them without
        # tripping a lazy-load greenlet error after commit.
        await self._session.refresh(sensor)
        return sensor

    async def update(
        self,
        sensor: Sensor,
        *,
        name: str | None = None,
        config_json: dict[str, Any] | None = None,
        target_pipeline_id: UUID | None = None,
        poll_interval_seconds: int | None = None,
        is_active: bool | None = None,
    ) -> Sensor:
        if name is not None and name != sensor.name:
            existing = await self.get_by_name(workspace_id=sensor.workspace_id, name=name)
            if existing is not None and existing.id != sensor.id:
                raise SensorNameTakenError(name)
            sensor.name = name
        if config_json is not None:
            sensor.config_json = config_json
        # NOTE: target_pipeline_id is intentionally settable to None ("orphan
        # the sensor" — keeps history, scheduler skips). The router treats
        # an absent JSON field as "no change", a JSON ``null`` as "clear".
        if target_pipeline_id is not _UNSET:
            sensor.target_pipeline_id = target_pipeline_id
        if poll_interval_seconds is not None:
            sensor.poll_interval_seconds = poll_interval_seconds
        if is_active is not None:
            sensor.is_active = is_active
        await self._session.flush()
        await self._session.refresh(sensor)
        return sensor

    async def delete(self, sensor: Sensor) -> None:
        await self._session.delete(sensor)
        await self._session.flush()

    # ---- scheduler hooks ---------------------------------------------------

    async def record_check(
        self,
        sensor: Sensor,
        *,
        now: datetime,
        triggered: bool,
        result_json: dict[str, Any] | None,
    ) -> None:
        """Stamp last_check_at + last_result_json. Bump last_triggered_at
        only if the check fired. Caller commits.

        Idempotency: the scheduler may re-poll a sensor whose previous result
        already fired; bumping ``last_triggered_at`` on every fire is what
        powers the UI's "last fired at" surface — de-duplication of trigger
        runs themselves lives in the scheduler's `should_skip` policy (TBD).
        """
        sensor.last_check_at = now
        sensor.last_result_json = result_json
        if triggered:
            sensor.last_triggered_at = now


# Sentinel so ``update(target_pipeline_id=None)`` can distinguish "clear it"
# (None) from "leave unchanged" (sentinel). Internal use only — the router
# passes ``_UNSET`` when its request body omits the field.
class _Unset:
    """Singleton sentinel for 'not provided' update arguments."""

    _instance: _Unset | None = None

    def __new__(cls) -> _Unset:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover — readability only
        return "<UNSET>"


_UNSET: Any = _Unset()


__all__ = [
    "_UNSET",
    "SensorNameTakenError",
    "SensorRepository",
    "UnknownSensorTypeError",
]
