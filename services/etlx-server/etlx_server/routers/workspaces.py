"""Workspace CRUD endpoints (Step 8.5a).

Endpoint matrix:

| Method | Path                     | Auth                                    |
|--------|--------------------------|-----------------------------------------|
| POST   | ``/workspaces``          | any authenticated user — they become Owner. |
| GET    | ``/workspaces``          | lists the caller's memberships; SuperAdmin sees all. |
| GET    | ``/workspaces/{id}``     | Viewer+ (Step 8.3 endpoint).            |
| PATCH  | ``/workspaces/{id}``     | Editor+ (workspace mutation).           |
| DELETE | ``/workspaces/{id}``     | Owner only (irreversible — cascades).   |

Every mutation records one audit row (``workspace.create`` /
``workspace.update`` / ``workspace.delete``) inside the same transaction
that performs the mutation, so a failure leaves no orphaned audit entry.
The router owns the commit boundary; ``get_session`` itself never
commits.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.audit.dependencies import get_audit_service
from etlx_server.audit.service import AuditService
from etlx_server.auth.current_user import get_current_user
from etlx_server.auth.schemas import (
    CurrentUser,
    WorkspaceCreateRequest,
    WorkspaceSummary,
    WorkspaceUpdateRequest,
)
from etlx_server.auth.workspace_context import (
    WorkspaceContext,
    require_workspace_role,
)
from etlx_server.auth.workspace_repository import (
    WorkspaceRepository,
    WorkspaceSlugTakenError,
)
from etlx_server.db.enums import WorkspaceRole
from etlx_server.dependencies import get_session

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

# Module-level Depends singletons — ruff B008 disallows function calls in
# default-arg position. One per minimum role keeps the policy declarative.
_require_viewer = Depends(require_workspace_role(WorkspaceRole.VIEWER))
_require_editor = Depends(require_workspace_role(WorkspaceRole.EDITOR))
_require_owner = Depends(require_workspace_role(WorkspaceRole.OWNER))


@router.post("", response_model=WorkspaceSummary, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreateRequest,
    user: CurrentUser = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> WorkspaceSummary:
    """Create a workspace; caller becomes Owner."""
    repo = WorkspaceRepository(session)
    try:
        workspace = await repo.create(
            name=body.name,
            slug=body.slug,
            color_hex=body.color_hex,
            owner_user_id=user.id,
        )
    except WorkspaceSlugTakenError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    await audit.record(
        actor_user_id=user.id,
        workspace_id=workspace.id,
        action="workspace.create",
        resource_type="workspace",
        resource_id=str(workspace.id),
        before=None,
        after=WorkspaceRepository.snapshot(workspace),
    )
    await session.commit()
    return WorkspaceSummary(
        id=workspace.id,
        name=workspace.name,
        slug=workspace.slug,
        color_hex=workspace.color_hex,
        role=WorkspaceRole.OWNER.value,
    )


@router.get("", response_model=list[WorkspaceSummary])
async def list_workspaces(
    user: CurrentUser = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[WorkspaceSummary]:
    """List workspaces the caller belongs to (SuperAdmin sees all)."""
    repo = WorkspaceRepository(session)
    workspaces = await repo.list_for_user(user_id=user.id, include_all=user.is_superadmin)
    # We don't surface ``role`` here to avoid an N+1 — the FE can hit
    # ``GET /workspaces/{id}`` for that. SuperAdmin sees role=None on
    # workspaces they're not a member of, ordinary users only see ones
    # they are.
    return [
        WorkspaceSummary(id=w.id, name=w.name, slug=w.slug, color_hex=w.color_hex, role=None)
        for w in workspaces
    ]


@router.get("/{workspace_id}", response_model=WorkspaceSummary)
async def get_workspace(
    ctx: WorkspaceContext = _require_viewer,
) -> WorkspaceSummary:
    return WorkspaceSummary(
        id=ctx.workspace.id,
        name=ctx.workspace.name,
        slug=ctx.workspace.slug,
        color_hex=ctx.workspace.color_hex,
        role=ctx.role.value if ctx.role is not None else None,
    )


@router.patch("/{workspace_id}", response_model=WorkspaceSummary)
async def update_workspace(
    body: WorkspaceUpdateRequest,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> WorkspaceSummary:
    fields = body.as_field_dict()
    if not fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one field is required",
        )
    before = WorkspaceRepository.snapshot(ctx.workspace, fields.keys())
    repo = WorkspaceRepository(session)
    try:
        updated = await repo.update(ctx.workspace, **fields)
    except WorkspaceSlugTakenError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    after = WorkspaceRepository.snapshot(updated, fields.keys())

    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=updated.id,
        action="workspace.update",
        resource_type="workspace",
        resource_id=str(updated.id),
        before=before,
        after=after,
    )
    await session.commit()
    return WorkspaceSummary(
        id=updated.id,
        name=updated.name,
        slug=updated.slug,
        color_hex=updated.color_hex,
        role=ctx.role.value if ctx.role is not None else None,
    )


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    ctx: WorkspaceContext = _require_owner,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> None:
    before = WorkspaceRepository.snapshot(ctx.workspace)
    # Audit row first — its workspace_id FK is ``ON DELETE SET NULL``, so the
    # row survives the workspace removal and stays queryable for forensics.
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="workspace.delete",
        resource_type="workspace",
        resource_id=str(ctx.workspace.id),
        before=before,
        after=None,
    )
    await WorkspaceRepository(session).delete(ctx.workspace)
    await session.commit()
