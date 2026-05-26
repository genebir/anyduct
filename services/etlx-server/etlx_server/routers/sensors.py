"""Sensor CRUD + manual check (ADR-0041 K3b).

| Method | Path                                                  | Auth     |
|--------|-------------------------------------------------------|----------|
| GET    | ``/workspaces/{ws}/sensors``                          | Viewer+  |
| POST   | ``/workspaces/{ws}/sensors``                          | Editor+  |
| GET    | ``/workspaces/{ws}/sensors/{sid}``                    | Viewer+  |
| PATCH  | ``/workspaces/{ws}/sensors/{sid}``                    | Editor+  |
| DELETE | ``/workspaces/{ws}/sensors/{sid}``                    | Editor+  |
| POST   | ``/workspaces/{ws}/sensors/{sid}/check``              | Viewer+  |

The ``check`` endpoint runs the configured sensor's ``check()`` once
and returns the result — it does NOT enqueue a trigger run. Useful for
the UI's "did I configure this right?" debug button. Production
trigger runs flow through the ``SensorScheduler`` tick loop.

Sensors live at workspace level (not nested under a pipeline) because
their ``target_pipeline_id`` can be moved across pipelines and the
sensor history shouldn't follow. Cross-workspace access is blocked by
the standard ``require_workspace_role`` dependency.
"""

from __future__ import annotations

import contextlib
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Import side-effects: register every built-in sensor so build_sensor can
# dispatch to it even when this router is the first thing loaded.
#  * ``etl_plugins.sensors``  — pure-core builtins (``http``)
#  * ``etlx_server.sensors.builtins`` — service-side builtins that need
#    DB access (``asset_freshness``)
import etl_plugins.sensors  # noqa: F401
import etlx_server.sensors.builtins  # noqa: F401
from etl_plugins.config.secrets import SecretBackend
from etl_plugins.core.exceptions import ConfigError
from etl_plugins.core.sensor import build_sensor, registered_sensor_types
from etlx_server.audit.dependencies import get_audit_service
from etlx_server.audit.service import AuditService
from etlx_server.auth.schemas import (
    SensorCheckResponse,
    SensorCreateRequest,
    SensorSummary,
    SensorUpdateRequest,
)
from etlx_server.auth.workspace_context import (
    WorkspaceContext,
    require_workspace_role,
)
from etlx_server.db.enums import WorkspaceRole
from etlx_server.db.models import Sensor
from etlx_server.dependencies import get_secret_backend_dep, get_session, get_session_factory
from etlx_server.sensors.context import use_sensor_context
from etlx_server.sensors.repository import (
    _UNSET,
    SensorNameTakenError,
    SensorRepository,
    UnknownSensorTypeError,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/sensors", tags=["sensors"])

_require_viewer = Depends(require_workspace_role(WorkspaceRole.VIEWER))
_require_editor = Depends(require_workspace_role(WorkspaceRole.EDITOR))


def _to_summary(sensor: Sensor) -> SensorSummary:
    return SensorSummary.model_validate(sensor)


def _snapshot(sensor: Sensor) -> dict[str, Any]:
    """Audit payload — small JSON-serialisable view of the row."""
    return {
        "name": sensor.name,
        "type": sensor.type,
        "config_json": sensor.config_json,
        "target_pipeline_id": str(sensor.target_pipeline_id) if sensor.target_pipeline_id else None,
        "poll_interval_seconds": sensor.poll_interval_seconds,
        "is_active": sensor.is_active,
    }


# ---- read --------------------------------------------------------------------


@router.get("", response_model=list[SensorSummary])
async def list_sensors(
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[SensorSummary]:
    rows = await SensorRepository(session).list_for_workspace(workspace_id=ctx.workspace.id)
    return [_to_summary(r) for r in rows]


@router.get("/{sensor_id}", response_model=SensorSummary)
async def get_sensor(
    sensor_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SensorSummary:
    sensor = await _resolve_or_404(session, workspace_id=ctx.workspace.id, sensor_id=sensor_id)
    return _to_summary(sensor)


# ---- write -------------------------------------------------------------------


@router.post("", response_model=SensorSummary, status_code=status.HTTP_201_CREATED)
async def create_sensor(
    body: SensorCreateRequest,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> SensorSummary:
    repo = SensorRepository(session)
    try:
        sensor = await repo.create(
            workspace_id=ctx.workspace.id,
            name=body.name,
            sensor_type=body.type,
            config_json=body.config_json,
            target_pipeline_id=body.target_pipeline_id,
            poll_interval_seconds=body.poll_interval_seconds,
            is_active=body.is_active,
            created_by_user_id=ctx.user.id,
        )
    except SensorNameTakenError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"sensor name already in use in this workspace: {e}",
        ) from e
    except UnknownSensorTypeError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(f"unknown sensor type {e!s}; registered: {registered_sensor_types()}"),
        ) from e
    # Validate the config by building the sensor once (rejects e.g. missing
    # ``url`` for http). On bad config, rolls back the insert so we don't
    # persist an unrunnable row.
    try:
        instance = build_sensor(sensor.type, sensor.config_json or {})
    except ConfigError as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"invalid sensor config: {e}",
        ) from e
    else:
        close = getattr(instance, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()

    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="sensor.create",
        resource_type="sensor",
        resource_id=str(sensor.id),
        before=None,
        after=_snapshot(sensor),
    )
    await session.commit()
    return _to_summary(sensor)


@router.patch("/{sensor_id}", response_model=SensorSummary)
async def update_sensor(
    sensor_id: UUID,
    body: SensorUpdateRequest,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> SensorSummary:
    sensor = await _resolve_or_404(session, workspace_id=ctx.workspace.id, sensor_id=sensor_id)
    fields = body.as_field_dict()
    if not fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one field is required",
        )

    before = _snapshot(sensor)
    repo = SensorRepository(session)
    try:
        # `target_pipeline_id` semantics: omitted → unchanged; explicit
        # null → clear. The repo's sentinel-based update handles this.
        updated = await repo.update(
            sensor,
            name=fields.get("name"),
            config_json=fields.get("config_json"),
            target_pipeline_id=fields.get("target_pipeline_id", _UNSET),
            poll_interval_seconds=fields.get("poll_interval_seconds"),
            is_active=fields.get("is_active"),
        )
    except SensorNameTakenError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"sensor name already in use in this workspace: {e}",
        ) from e

    # If config_json changed, validate it by building (same posture as create).
    if "config_json" in fields:
        try:
            instance = build_sensor(updated.type, updated.config_json or {})
        except ConfigError as e:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"invalid sensor config: {e}",
            ) from e
        else:
            close = getattr(instance, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()

    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="sensor.update",
        resource_type="sensor",
        resource_id=str(updated.id),
        before=before,
        after=_snapshot(updated),
    )
    await session.commit()
    return _to_summary(updated)


@router.delete("/{sensor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sensor(
    sensor_id: UUID,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> None:
    sensor = await _resolve_or_404(session, workspace_id=ctx.workspace.id, sensor_id=sensor_id)
    before = _snapshot(sensor)
    await SensorRepository(session).delete(sensor)
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="sensor.delete",
        resource_type="sensor",
        resource_id=str(sensor_id),
        before=before,
        after=None,
    )
    await session.commit()


# ---- manual check ----------------------------------------------------------


_require_viewer_session_factory = Depends(get_session_factory)
_require_viewer_secret_backend = Depends(get_secret_backend_dep)


@router.post("/{sensor_id}/check", response_model=SensorCheckResponse)
async def check_sensor(
    sensor_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    session_factory: async_sessionmaker[AsyncSession] = _require_viewer_session_factory,
    secret_backend: SecretBackend = _require_viewer_secret_backend,
) -> SensorCheckResponse:
    """Run the sensor's check once and return the result. Does NOT enqueue
    a trigger run — that's the scheduler's job. Useful for the builder
    UI's "test" button before saving.

    Routes through :func:`use_sensor_context` so service-aware sensors
    (asset_freshness) can read the DB session factory + workspace id
    from ContextVars exactly like they do in the scheduler tick — the
    "Check now" button result matches what production would do."""
    sensor = await _resolve_or_404(session, workspace_id=ctx.workspace.id, sensor_id=sensor_id)
    try:
        instance = build_sensor(sensor.type, sensor.config_json or {})
    except ConfigError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"invalid sensor config: {e}",
        ) from e
    try:
        async with use_sensor_context(
            session_factory=session_factory,
            workspace_id=ctx.workspace.id,
            # Pass the stored last_triggered_at so de-dupe-aware sensors
            # (lineage_arrival) return the same answer in the "Check now"
            # button that they would on the next production tick.
            last_triggered_at=sensor.last_triggered_at,
            # SecretBackend so connection-referencing sensors (file_landed)
            # can resolve placeholders inside the linked Connection's
            # config the same way the scheduler does.
            secret_backend=secret_backend,
        ):
            result = await instance.check_async()
    finally:
        close = getattr(instance, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()
    return SensorCheckResponse(
        triggered=result.triggered,
        message=result.message,
        metadata=dict(result.metadata) if result.metadata else {},
    )


# ---- helpers ---------------------------------------------------------------


async def _resolve_or_404(session: AsyncSession, *, workspace_id: UUID, sensor_id: UUID) -> Sensor:
    sensor = await SensorRepository(session).get(workspace_id=workspace_id, sensor_id=sensor_id)
    if sensor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sensor not found")
    return sensor
