"""Runs read-mostly + log streaming + retry (Step 8.5e + 8.6).

| Method | Path                                                  | Auth     |
|--------|-------------------------------------------------------|----------|
| GET    | ``/workspaces/{ws}/runs``                             | Viewer+  |
| GET    | ``/workspaces/{ws}/runs/{rid}``                       | Viewer+  |
| GET    | ``/workspaces/{ws}/runs/{rid}/logs``                  | Viewer+  |
| GET    | ``/workspaces/{ws}/runs/{rid}/logs/stream``           | Viewer+  |
| GET    | ``/workspaces/{ws}/runs/{rid}/metrics``               | Viewer+  |
| POST   | ``/workspaces/{ws}/runs/{rid}/retry``                 | Runner+  |

Status mutations on existing runs (``pending`` → ``running`` →
terminal) belong to the worker engine (Step 9). The only HTTP write
here is ``POST /retry``, which *creates a new pending row* (it does
not touch the original). That keeps the worker as the single writer
for status transitions while still letting the UI hand the user a
"try again" button.

The streaming logs endpoint emits Server-Sent Events. Each event is a
single ``RunLogEntry`` JSON object. The stream ends after the run
reaches a terminal state and no new log lines arrive within the idle
window — so a UI tab subscribed to a still-running pipeline keeps
receiving updates and naturally completes once the worker is done.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from etlx_server.audit.dependencies import get_audit_service
from etlx_server.audit.service import AuditService
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
from etlx_server.dependencies import get_session, get_session_factory
from etlx_server.runs.repository import RunNotRetryableError, RunRepository

router = APIRouter(prefix="/workspaces/{workspace_id}/runs", tags=["runs"])

_require_viewer = Depends(require_workspace_role(WorkspaceRole.VIEWER))
_require_runner = Depends(require_workspace_role(WorkspaceRole.RUNNER))

_TERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
)
# Poll cadence + idle window for the SSE stream. Keep the cadence
# short enough that human readers see near-real-time updates without
# DoS-ing the DB.
_STREAM_POLL_SECONDS = 0.5
_STREAM_IDLE_SECONDS_AFTER_TERMINAL = 2.0


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


# --- Action endpoints (Step 8.6) -------------------------------------------


def _log_to_sse_event(entry: RunLogEntry) -> str:
    """Serialize one log row as a Server-Sent Event frame.

    SSE is a tiny text protocol: an ``event:`` line names the channel
    and a ``data:`` line carries one JSON line; two ``\\n`` ends the
    frame. We keep the event name explicit (``log``) so the UI can
    distinguish from future channels (e.g. ``status``).
    """
    payload = entry.model_dump(mode="json")
    return f"event: log\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


async def _stream_run_logs(
    *,
    request: Request,
    factory: async_sessionmaker[AsyncSession],
    workspace_id: UUID,
    run_id: UUID,
    start_after: datetime | None,
) -> AsyncIterator[bytes]:
    """Yield SSE-encoded log frames until the run is terminal + idle.

    Opens a fresh ``AsyncSession`` per poll iteration so we never hold
    a long-running transaction during the stream. The terminal+idle
    rule means a still-running pipeline keeps the connection open and
    a finished one closes it after a short grace window — so the UI
    sees the last few buffered log lines after status flips.
    """
    last_seen_ts = start_after
    terminal_since: datetime | None = None
    while True:
        if await request.is_disconnected():
            return

        new_entries: list[RunLogEntry] = []
        run_status: RunStatus | None = None
        async with factory() as session:
            repo = RunRepository(session)
            run = await repo.get(workspace_id=workspace_id, run_id=run_id)
            if run is None:
                # Row disappeared mid-stream — treat as graceful end-of-stream.
                return
            run_status = run.status
            # We re-fetch the full ordered log set and slice by timestamp.
            # The volume per run is bounded; if it grows we can switch
            # to a ``ts > last_seen_ts`` filter pushed into SQL.
            logs = await repo.list_logs(run_id=run.id, limit=1000)
            for row in logs:
                if last_seen_ts is None or row.ts > last_seen_ts:
                    new_entries.append(RunLogEntry.model_validate(row))

        if new_entries:
            for entry in new_entries:
                yield _log_to_sse_event(entry).encode("utf-8")
            last_seen_ts = new_entries[-1].ts
            # Receiving new events resets the post-terminal idle window —
            # we want to drain anything the worker buffered before exit.
            if run_status in _TERMINAL_STATUSES:
                terminal_since = datetime.now(tz=last_seen_ts.tzinfo if last_seen_ts else None)
        elif run_status in _TERMINAL_STATUSES:
            now = datetime.now(tz=last_seen_ts.tzinfo if last_seen_ts else None)
            if terminal_since is None:
                terminal_since = now
            elif (now - terminal_since).total_seconds() >= _STREAM_IDLE_SECONDS_AFTER_TERMINAL:
                return

        await asyncio.sleep(_STREAM_POLL_SECONDS)


@router.get("/{run_id}/logs/stream")
async def stream_run_logs(
    run_id: UUID,
    request: Request,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),  # noqa: B008
) -> StreamingResponse:
    """SSE stream of new run_logs as the worker emits them.

    Boundary check happens up front with the request-scoped session;
    the stream itself uses a fresh session per poll so it doesn't tie
    up a connection for the connection's whole lifetime.
    """
    repo = RunRepository(session)
    run = await repo.get(workspace_id=ctx.workspace.id, run_id=run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    # Replay any existing logs from the start of time.
    return StreamingResponse(
        _stream_run_logs(
            request=request,
            factory=factory,
            workspace_id=ctx.workspace.id,
            run_id=run.id,
            start_after=None,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{run_id}/retry",
    response_model=RunSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_run(
    run_id: UUID,
    ctx: WorkspaceContext = _require_runner,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> RunSummary:
    """Enqueue a fresh pending Run that mirrors a failed/cancelled one.

    The new row shares ``pipeline_version_id`` + ``schedule_id`` with
    the original (we retry *what was attempted*, not the current
    config). ``result_json.retry_of`` carries the link back for
    forensics; no fields on the original row are touched.
    """
    repo = RunRepository(session)
    original = await repo.get(workspace_id=ctx.workspace.id, run_id=run_id)
    if original is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    try:
        new_run = await repo.add_retry(original, triggered_by_user_id=ctx.user.id)
    except RunNotRetryableError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="run.retry",
        resource_type="run",
        resource_id=str(new_run.id),
        before=None,
        after={
            "retry_of": str(original.id),
            "pipeline_id": str(original.pipeline_id),
            "pipeline_version_id": str(original.pipeline_version_id),
        },
    )
    await session.commit()
    return RunSummary.model_validate(new_run)
