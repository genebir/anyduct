"""Audit log query API (Step 8.4, ADR-0023 §9).

Single endpoint ``GET /audit`` with two access modes:

* ``?workspace_id=<id>`` — must be a member (Viewer or above) of that
  workspace, *or* a SuperAdmin. Returns rows scoped to that workspace.
* no ``workspace_id`` — SuperAdmin only. Returns rows across all
  workspaces; useful for platform-operator incident triage.

Filter knobs: ``actor_user_id`` / ``resource_type`` / ``resource_id`` /
``limit`` (1-200) / ``offset``.

Rows are returned newest-first. Per Step 8.4's scope this is a read-only
slice; Step 8.5 mutating endpoints will produce the rows by calling
:class:`AuditService.record`.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.audit.repository import AuditLogRepository
from etlx_server.auth.current_user import get_current_user
from etlx_server.auth.membership_repository import MembershipRepository
from etlx_server.auth.schemas import AuditLogEntry, CurrentUser
from etlx_server.dependencies import get_session

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogEntry])
async def query_audit_log(
    workspace_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    resource_type: str | None = Query(default=None, max_length=64),
    resource_id: str | None = Query(default=None, max_length=64),
    action: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: CurrentUser = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[AuditLogEntry]:
    if workspace_id is None:
        if not user.is_superadmin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="cross-workspace audit query requires SuperAdmin",
            )
    elif not user.is_superadmin:
        role = await MembershipRepository(session).get_role(
            workspace_id=workspace_id, user_id=user.id
        )
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="not a member of this workspace",
            )

    rows = await AuditLogRepository(session).query(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        limit=limit,
        offset=offset,
    )
    return [AuditLogEntry.model_validate(row) for row in rows]
