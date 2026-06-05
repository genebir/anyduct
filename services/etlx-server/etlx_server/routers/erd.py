"""ERD diagram CRUD (Phase AHD, ADR-0090).

| Method | Path                                          | Auth    |
|--------|-----------------------------------------------|---------|
| GET    | ``/workspaces/{ws}/erd-diagrams``             | Viewer+ |
| POST   | ``/workspaces/{ws}/erd-diagrams``             | Editor+ |
| GET    | ``/workspaces/{ws}/erd-diagrams/{id}``        | Viewer+ |
| PATCH  | ``/workspaces/{ws}/erd-diagrams/{id}``        | Editor+ |
| DELETE | ``/workspaces/{ws}/erd-diagrams/{id}``        | Editor+ |

Server-persisted, workspace-scoped ERDs so diagrams are durable + shared
(like pipelines), not browser-local. ``design_json`` is the opaque
designer model — the server stores it verbatim. Mutations pair an audit row.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.audit.dependencies import get_audit_service
from etlx_server.audit.service import AuditService
from etlx_server.auth.schemas import (
    ErdDiagramCreateRequest,
    ErdDiagramDetail,
    ErdDiagramSummary,
    ErdDiagramUpdateRequest,
)
from etlx_server.auth.workspace_context import WorkspaceContext, require_workspace_role
from etlx_server.db.enums import WorkspaceRole
from etlx_server.db.models import ErdDiagram
from etlx_server.dependencies import get_session
from etlx_server.erd.repository import ErdDiagramRepository

router = APIRouter(prefix="/workspaces/{workspace_id}/erd-diagrams", tags=["erd"])

_require_viewer = Depends(require_workspace_role(WorkspaceRole.VIEWER))
_require_editor = Depends(require_workspace_role(WorkspaceRole.EDITOR))


def _table_count(design: dict[str, Any] | None) -> int:
    tables = (design or {}).get("tables")
    return len(tables) if isinstance(tables, list) else 0


def _summary(row: ErdDiagram) -> ErdDiagramSummary:
    return ErdDiagramSummary(
        id=row.id,
        name=row.name,
        table_count=_table_count(row.design_json),
        updated_at=row.updated_at,
    )


@router.get("", response_model=list[ErdDiagramSummary])
async def list_diagrams(
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[ErdDiagramSummary]:
    rows = await ErdDiagramRepository(session).list_for_workspace(workspace_id=ctx.workspace.id)
    return [_summary(r) for r in rows]


@router.post("", response_model=ErdDiagramDetail, status_code=status.HTTP_201_CREATED)
async def create_diagram(
    body: ErdDiagramCreateRequest,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> ErdDiagramDetail:
    row = await ErdDiagramRepository(session).create(
        workspace_id=ctx.workspace.id,
        name=body.name,
        design_json=body.design_json,
        created_by_user_id=ctx.user.id,
    )
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="erd.create",
        resource_type="erd_diagram",
        resource_id=str(row.id),
        before=None,
        after={"name": row.name},
    )
    await session.commit()
    await session.refresh(row)
    return ErdDiagramDetail.model_validate(row)


async def _get_or_404(session: AsyncSession, ctx: WorkspaceContext, diagram_id: UUID) -> ErdDiagram:
    row = await ErdDiagramRepository(session).get(
        workspace_id=ctx.workspace.id, diagram_id=diagram_id
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ERD diagram not found")
    return row


@router.get("/{diagram_id}", response_model=ErdDiagramDetail)
async def get_diagram(
    diagram_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ErdDiagramDetail:
    row = await _get_or_404(session, ctx, diagram_id)
    return ErdDiagramDetail.model_validate(row)


@router.patch("/{diagram_id}", response_model=ErdDiagramDetail)
async def update_diagram(
    diagram_id: UUID,
    body: ErdDiagramUpdateRequest,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> ErdDiagramDetail:
    repo = ErdDiagramRepository(session)
    row = await _get_or_404(session, ctx, diagram_id)
    await repo.update(row, name=body.name, design_json=body.design_json)
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="erd.update",
        resource_type="erd_diagram",
        resource_id=str(row.id),
        before=None,
        after={"name": row.name},
    )
    await session.commit()
    await session.refresh(row)
    return ErdDiagramDetail.model_validate(row)


@router.delete("/{diagram_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_diagram(
    diagram_id: UUID,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> None:
    repo = ErdDiagramRepository(session)
    row = await _get_or_404(session, ctx, diagram_id)
    await repo.delete(row)
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="erd.delete",
        resource_type="erd_diagram",
        resource_id=str(diagram_id),
        before={"name": row.name},
        after=None,
    )
    await session.commit()
