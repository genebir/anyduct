"""Workspace membership endpoints (Step 8.5b).

Mounted under each workspace:

| Method | Path                                                | Auth      |
|--------|-----------------------------------------------------|-----------|
| GET    | ``/workspaces/{ws}/memberships``                    | Viewer+   |
| POST   | ``/workspaces/{ws}/memberships``                    | Owner     |
| PATCH  | ``/workspaces/{ws}/memberships/{user_id}``          | Owner     |
| DELETE | ``/workspaces/{ws}/memberships/{user_id}``          | Owner     |

Last-Owner safeguards live in :class:`MembershipRepository.update_role` /
``remove`` — demoting or removing the only Owner raises
:class:`LastOwnerError`, which the router lifts into a 409. This applies
equally to self-mutation: an Owner can leave the workspace only if at
least one other Owner remains.

Audit rows pair every mutation:

* ``membership.create`` (after = {user_id, role})
* ``membership.update`` (before/after = {role})
* ``membership.delete`` (before = {user_id, role})
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.audit.dependencies import get_audit_service
from etlx_server.audit.service import AuditService
from etlx_server.auth.membership_repository import (
    LastOwnerError,
    MembershipExistsError,
    MembershipRepository,
)
from etlx_server.auth.schemas import (
    MembershipCreateRequest,
    MembershipSummary,
    MembershipUpdateRequest,
)
from etlx_server.auth.user_repository import UserRepository
from etlx_server.auth.workspace_context import (
    WorkspaceContext,
    require_workspace_role,
)
from etlx_server.db.enums import WorkspaceRole
from etlx_server.dependencies import get_session

router = APIRouter(prefix="/workspaces/{workspace_id}/memberships", tags=["memberships"])

_require_viewer = Depends(require_workspace_role(WorkspaceRole.VIEWER))
_require_owner = Depends(require_workspace_role(WorkspaceRole.OWNER))


@router.get("", response_model=list[MembershipSummary])
async def list_memberships(
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[MembershipSummary]:
    rows = await MembershipRepository(session).list_for_workspace(workspace_id=ctx.workspace.id)
    return [
        MembershipSummary(
            id=membership.id,
            user_id=user.id,
            email=user.email,
            name=user.name,
            role=membership.role.value,
        )
        for membership, user in rows
    ]


@router.post("", response_model=MembershipSummary, status_code=status.HTTP_201_CREATED)
async def add_membership(
    body: MembershipCreateRequest,
    ctx: WorkspaceContext = _require_owner,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> MembershipSummary:
    user = await UserRepository(session).get_by_email(body.email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no user with email {body.email}",
        )
    new_role = WorkspaceRole(body.role)
    repo = MembershipRepository(session)
    try:
        membership = await repo.add(workspace_id=ctx.workspace.id, user_id=user.id, role=new_role)
    except MembershipExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="membership.create",
        resource_type="membership",
        resource_id=str(membership.id),
        before=None,
        after={"user_id": str(user.id), "role": new_role.value},
    )
    await session.commit()
    return MembershipSummary(
        id=membership.id,
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=new_role.value,
    )


@router.patch("/{user_id}", response_model=MembershipSummary)
async def update_membership(
    user_id: UUID,
    body: MembershipUpdateRequest,
    ctx: WorkspaceContext = _require_owner,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> MembershipSummary:
    repo = MembershipRepository(session)
    membership = await repo.get(workspace_id=ctx.workspace.id, user_id=user_id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="membership not found")
    target_user = await UserRepository(session).get_by_id(user_id)
    # Membership FK guarantees user exists, but guard anyway for clearer typing.
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    new_role = WorkspaceRole(body.role)
    before = {"role": membership.role.value}
    try:
        await repo.update_role(membership, new_role)
    except LastOwnerError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    after = {"role": new_role.value}

    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="membership.update",
        resource_type="membership",
        resource_id=str(membership.id),
        before=before,
        after=after,
    )
    await session.commit()
    return MembershipSummary(
        id=membership.id,
        user_id=target_user.id,
        email=target_user.email,
        name=target_user.name,
        role=new_role.value,
    )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_membership(
    user_id: UUID,
    ctx: WorkspaceContext = _require_owner,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> None:
    repo = MembershipRepository(session)
    membership = await repo.get(workspace_id=ctx.workspace.id, user_id=user_id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="membership not found")
    # Snapshot before delete; ``membership.id`` survives the row removal on the
    # Python side, so the audit row can still reference it.
    before = {"user_id": str(membership.user_id), "role": membership.role.value}
    membership_id = membership.id
    try:
        await repo.remove(membership)
    except LastOwnerError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="membership.delete",
        resource_type="membership",
        resource_id=str(membership_id),
        before=before,
        after=None,
    )
    await session.commit()
