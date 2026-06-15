"""Workspace-global variable CRUD (ADR-0041, V2).

| Method | Path                                          | Auth    |
|--------|-----------------------------------------------|---------|
| GET    | ``/workspaces/{ws}/variables``                | Viewer+ |
| PUT    | ``/workspaces/{ws}/variables/{name}``         | Editor+ |
| DELETE | ``/workspaces/{ws}/variables/{name}``         | Editor+ |

Globals are referenced in pipeline configs as ``${var.name}`` and merge *under*
a pipeline's local ``variables`` (locals win). They are non-secret config —
sensitive values belong in the secret backend. Every mutation pairs an audit row.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.audit.dependencies import get_audit_service
from anyduct_server.audit.service import AuditService
from anyduct_server.auth.schemas import WorkspaceVariableEntry, WorkspaceVariableSetRequest
from anyduct_server.auth.workspace_context import WorkspaceContext, require_workspace_role
from anyduct_server.db.enums import WorkspaceRole
from anyduct_server.db.models import WorkspaceVariable
from anyduct_server.dependencies import get_session
from anyduct_server.variables.repository import WorkspaceVariableRepository

router = APIRouter(prefix="/workspaces/{workspace_id}/variables", tags=["variables"])

_require_viewer = Depends(require_workspace_role(WorkspaceRole.VIEWER))
_require_editor = Depends(require_workspace_role(WorkspaceRole.EDITOR))

# Must be a Python-identifier-like name so it's referenceable as ``${var.name}``.
_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _to_entry(row: WorkspaceVariable) -> WorkspaceVariableEntry:
    return WorkspaceVariableEntry(name=row.name, value=row.value_json, description=row.description)


@router.get("", response_model=list[WorkspaceVariableEntry])
async def list_variables(
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[WorkspaceVariableEntry]:
    rows = await WorkspaceVariableRepository(session).list_for_workspace(
        workspace_id=ctx.workspace.id
    )
    return [_to_entry(r) for r in rows]


@router.put("/{name}", response_model=WorkspaceVariableEntry)
async def set_variable(
    name: str,
    body: WorkspaceVariableSetRequest,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> WorkspaceVariableEntry:
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="variable name must be a valid identifier (referenced as ${var.name})",
        )
    repo = WorkspaceVariableRepository(session)
    row, created = await repo.set(
        workspace_id=ctx.workspace.id,
        name=name,
        value=body.value,
        description=body.description,
    )
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="variable.create" if created else "variable.update",
        resource_type="workspace_variable",
        resource_id=name,
        before=None,
        after={"name": name, "description": body.description},
    )
    await session.commit()
    return _to_entry(row)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_variable(
    name: str,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> None:
    repo = WorkspaceVariableRepository(session)
    row = await repo.get(workspace_id=ctx.workspace.id, name=name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="variable not found")
    await repo.delete(row)
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="variable.delete",
        resource_type="workspace_variable",
        resource_id=name,
        before={"name": name},
        after=None,
    )
    await session.commit()
