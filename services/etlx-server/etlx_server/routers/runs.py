"""Runs (read-only) (Step 8.5e).

| Method | Path                                                | Auth     |
|--------|-----------------------------------------------------|----------|
| GET    | ``/workspaces/{ws}/runs``                           | Viewer+  |
| GET    | ``/workspaces/{ws}/runs/{rid}``                     | Viewer+  |
| GET    | ``/workspaces/{ws}/runs/{rid}/logs``                | Viewer+  |
| GET    | ``/workspaces/{ws}/runs/{rid}/metrics``             | Viewer+  |

Runs are written exclusively by the worker engine (Step 9); the HTTP
surface is read-only. There's no audit log for these endpoints — they
don't mutate anything, and the run timeline itself *is* the audit trail
for what the engine did.

Filters on the list endpoint are intentionally narrow (``status``,
``pipeline_id``, ``schedule_id``, ``limit``, ``offset``); the UI table
drives any expansion. Pagination is offset-based for simplicity; once
the runs table grows past a few thousand per workspace we can switch
to keyset pagination without breaking the response shape.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.auth.schemas import (
    RunDetail,
    RunLogEntry,
    RunMetricEntry,
    RunSummary,
)
from etlx_server.auth.workspace_context import (
    WorkspaceContext,
    require_workspace_role,
)
from etlx_server.db.enums import RunStatus, WorkspaceRole
from etlx_server.dependencies import get_session
from etlx_server.runs.repository import RunRepository

router = APIRouter(prefix="/workspaces/{workspace_id}/runs", tags=["runs"])

_require_viewer = Depends(require_workspace_role(WorkspaceRole.VIEWER))


@router.get("", response_model=list[RunSummary])
async def list_runs(
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    status_filter: RunStatus | None = Query(default=None, alias="status"),  # noqa: B008
    pipeline_id: UUID | None = Query(default=None),  # noqa: B008
    schedule_id: UUID | None = Query(default=None),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[RunSummary]:
    rows = await RunRepository(session).list_for_workspace(
        workspace_id=ctx.workspace.id,
        status=status_filter,
        pipeline_id=pipeline_id,
        schedule_id=schedule_id,
        limit=limit,
        offset=offset,
    )
    return [RunSummary.model_validate(r) for r in rows]


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> RunDetail:
    run = await RunRepository(session).get(workspace_id=ctx.workspace.id, run_id=run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return RunDetail.model_validate(run)


@router.get("/{run_id}/logs", response_model=list[RunLogEntry])
async def list_run_logs(
    run_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[RunLogEntry]:
    repo = RunRepository(session)
    # Confirm the run belongs to this workspace before exposing its logs —
    # otherwise a workspace viewer could read another workspace's logs by
    # guessing a run UUID.
    run = await repo.get(workspace_id=ctx.workspace.id, run_id=run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    logs = await repo.list_logs(run_id=run.id, limit=limit, offset=offset)
    return [RunLogEntry.model_validate(row) for row in logs]


@router.get("/{run_id}/metrics", response_model=list[RunMetricEntry])
async def list_run_metrics(
    run_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[RunMetricEntry]:
    repo = RunRepository(session)
    run = await repo.get(workspace_id=ctx.workspace.id, run_id=run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    metrics = await repo.list_metrics(run_id=run.id)
    return [RunMetricEntry.model_validate(m) for m in metrics]
