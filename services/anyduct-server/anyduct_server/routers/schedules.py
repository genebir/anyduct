"""Schedule CRUD (Step 8.5e).

| Method | Path                                                          | Auth     |
|--------|---------------------------------------------------------------|----------|
| GET    | ``/workspaces/{ws}/pipelines/{pid}/schedules``                | Viewer+  |
| POST   | ``/workspaces/{ws}/pipelines/{pid}/schedules``                | Editor+  |
| GET    | ``/workspaces/{ws}/pipelines/{pid}/schedules/{sid}``          | Viewer+  |
| PATCH  | ``/workspaces/{ws}/pipelines/{pid}/schedules/{sid}``          | Editor+  |
| DELETE | ``/workspaces/{ws}/pipelines/{pid}/schedules/{sid}``          | Editor+  |
| POST   | ``/workspaces/{ws}/pipelines/{pid}/schedules/{sid}/toggle``   | Editor+  |

Schedules nest under their pipeline because the schema enforces 1-pipeline-many-schedules
and the worker engine (Step 9) treats them as policy attached to a specific
pipeline version. The router always cross-checks ``pipeline.workspace_id ==
ctx.workspace.id`` so a workspace member can't reach into schedules belonging
to another workspace's pipeline.

cron validation lives in :func:`anyduct_server.schedules.repository.validate_cron_for_mode`
— same library the worker will use to plan the next firing, so saving and
firing agree on what counts as a valid expression. ``mode`` is immutable
after creation; pipelines that need to switch between batch and stream
must recreate the schedule.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.audit.dependencies import get_audit_service
from anyduct_server.audit.service import AuditService
from anyduct_server.auth.schemas import (
    ScheduleCreateRequest,
    ScheduleSummary,
    ScheduleUpdateRequest,
)
from anyduct_server.auth.workspace_context import (
    WorkspaceContext,
    require_workspace_role,
)
from anyduct_server.db.enums import PipelineMode, WorkspaceRole
from anyduct_server.db.models import Schedule
from anyduct_server.dependencies import get_session
from anyduct_server.pipelines.repository import PipelineRepository
from anyduct_server.schedules.repository import (
    InvalidCronError,
    ScheduleRepository,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/pipelines/{pipeline_id}/schedules",
    tags=["schedules"],
)

_require_viewer = Depends(require_workspace_role(WorkspaceRole.VIEWER))
_require_editor = Depends(require_workspace_role(WorkspaceRole.EDITOR))


def _to_summary(schedule: Schedule) -> ScheduleSummary:
    return ScheduleSummary(
        id=schedule.id,
        pipeline_id=schedule.pipeline_id,
        name=schedule.name,
        mode=schedule.mode.value,
        cron_expr=schedule.cron_expr,
        is_active=schedule.is_active,
        config_overrides=schedule.config_overrides,
    )


async def _resolve_pipeline_or_404(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    pipeline_id: UUID,
) -> UUID:
    """Verify the pipeline belongs to the workspace; return its id.

    Returns the pipeline id (a UUID) for use as a FK in the repo calls.
    Raises 404 if the pipeline doesn't exist *in this workspace* — both
    "no such pipeline" and "cross-workspace reference" collapse to the
    same response, so a workspace member can't probe pipeline IDs
    belonging to other workspaces.
    """
    pipeline = await PipelineRepository(session).get(
        workspace_id=workspace_id, pipeline_id=pipeline_id
    )
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pipeline not found")
    return pipeline.id


@router.get("", response_model=list[ScheduleSummary])
async def list_schedules(
    pipeline_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[ScheduleSummary]:
    pid = await _resolve_pipeline_or_404(
        session, workspace_id=ctx.workspace.id, pipeline_id=pipeline_id
    )
    rows = await ScheduleRepository(session).list_for_pipeline(pipeline_id=pid)
    return [_to_summary(r) for r in rows]


@router.post("", response_model=ScheduleSummary, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    pipeline_id: UUID,
    body: ScheduleCreateRequest,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> ScheduleSummary:
    pid = await _resolve_pipeline_or_404(
        session, workspace_id=ctx.workspace.id, pipeline_id=pipeline_id
    )
    repo = ScheduleRepository(session)
    try:
        schedule = await repo.add(
            pipeline_id=pid,
            name=body.name,
            mode=PipelineMode(body.mode),
            cron_expr=body.cron_expr,
            is_active=body.is_active,
            config_overrides=body.config_overrides,
            created_by_user_id=ctx.user.id,
        )
    except InvalidCronError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e

    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="schedule.create",
        resource_type="schedule",
        resource_id=str(schedule.id),
        before=None,
        after=ScheduleRepository.snapshot(schedule),
    )
    await session.commit()
    return _to_summary(schedule)


@router.get("/{schedule_id}", response_model=ScheduleSummary)
async def get_schedule(
    pipeline_id: UUID,
    schedule_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ScheduleSummary:
    pid = await _resolve_pipeline_or_404(
        session, workspace_id=ctx.workspace.id, pipeline_id=pipeline_id
    )
    schedule = await ScheduleRepository(session).get(pipeline_id=pid, schedule_id=schedule_id)
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found")
    return _to_summary(schedule)


@router.patch("/{schedule_id}", response_model=ScheduleSummary)
async def update_schedule(
    pipeline_id: UUID,
    schedule_id: UUID,
    body: ScheduleUpdateRequest,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> ScheduleSummary:
    pid = await _resolve_pipeline_or_404(
        session, workspace_id=ctx.workspace.id, pipeline_id=pipeline_id
    )
    repo = ScheduleRepository(session)
    schedule = await repo.get(pipeline_id=pid, schedule_id=schedule_id)
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found")
    fields: dict[str, Any] = body.as_field_dict()
    if not fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one field is required",
        )

    before = ScheduleRepository.snapshot(schedule)
    try:
        updated = await repo.update(schedule, **fields)
    except InvalidCronError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e

    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="schedule.update",
        resource_type="schedule",
        resource_id=str(updated.id),
        before=before,
        after=ScheduleRepository.snapshot(updated),
    )
    await session.commit()
    return _to_summary(updated)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    pipeline_id: UUID,
    schedule_id: UUID,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> None:
    pid = await _resolve_pipeline_or_404(
        session, workspace_id=ctx.workspace.id, pipeline_id=pipeline_id
    )
    repo = ScheduleRepository(session)
    schedule = await repo.get(pipeline_id=pid, schedule_id=schedule_id)
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found")
    before = ScheduleRepository.snapshot(schedule)
    schedule_uuid = schedule.id
    await repo.delete(schedule)
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="schedule.delete",
        resource_type="schedule",
        resource_id=str(schedule_uuid),
        before=before,
        after=None,
    )
    await session.commit()


@router.post("/{schedule_id}/toggle", response_model=ScheduleSummary)
async def toggle_schedule(
    pipeline_id: UUID,
    schedule_id: UUID,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> ScheduleSummary:
    """Flip ``is_active`` — pause an active schedule, resume a paused one."""
    pid = await _resolve_pipeline_or_404(
        session, workspace_id=ctx.workspace.id, pipeline_id=pipeline_id
    )
    repo = ScheduleRepository(session)
    schedule = await repo.get(pipeline_id=pid, schedule_id=schedule_id)
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found")
    before = ScheduleRepository.snapshot(schedule)
    updated = await repo.update(schedule, is_active=not schedule.is_active)
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="schedule.toggle",
        resource_type="schedule",
        resource_id=str(updated.id),
        before=before,
        after=ScheduleRepository.snapshot(updated),
    )
    await session.commit()
    return _to_summary(updated)
