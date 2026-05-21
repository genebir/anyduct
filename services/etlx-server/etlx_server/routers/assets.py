"""Data catalog: assets + lineage (ADR-0036, Phase B3).

| Method | Path                                                  | Auth    |
|--------|-------------------------------------------------------|---------|
| GET    | ``/workspaces/{ws}/assets``                           | Viewer+ |
| GET    | ``/workspaces/{ws}/assets/{id}/lineage``              | Viewer+ |
| GET    | ``/workspaces/{ws}/assets/{id}/materializations``     | Viewer+ |

Read-only — the worker is the only writer (Phase B2). Assets are addressed by
their DB id (uuid), not the ``"conn/target"`` key, so the slash in the key
doesn't fight the URL path.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.assets.repository import AssetRepository
from etlx_server.auth.schemas import (
    AssetLineageResponse,
    AssetMaterializationEntry,
    AssetRef,
    AssetSummary,
)
from etlx_server.auth.workspace_context import WorkspaceContext, require_workspace_role
from etlx_server.db.enums import WorkspaceRole
from etlx_server.db.models import Asset
from etlx_server.dependencies import get_session

router = APIRouter(prefix="/workspaces/{workspace_id}/assets", tags=["assets"])

_require_viewer = Depends(require_workspace_role(WorkspaceRole.VIEWER))


def _ref(a: Asset) -> AssetRef:
    return AssetRef(id=a.id, asset_key=a.asset_key, kind=a.kind)


@router.get("", response_model=list[AssetSummary])
async def list_assets(
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[AssetSummary]:
    rows = await AssetRepository(session).list_for_workspace(workspace_id=ctx.workspace.id)
    return [AssetSummary.model_validate(r) for r in rows]


async def _resolve_or_404(session: AsyncSession, *, workspace_id: UUID, asset_id: UUID) -> Asset:
    asset = await AssetRepository(session).get(workspace_id=workspace_id, asset_id=asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    return asset


@router.get("/{asset_id}/lineage", response_model=AssetLineageResponse)
async def asset_lineage(
    asset_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> AssetLineageResponse:
    asset = await _resolve_or_404(session, workspace_id=ctx.workspace.id, asset_id=asset_id)
    repo = AssetRepository(session)
    return AssetLineageResponse(
        id=asset.id,
        asset_key=asset.asset_key,
        upstream=[_ref(a) for a in await repo.upstream(asset.id)],
        downstream=[_ref(a) for a in await repo.downstream(asset.id)],
    )


@router.get("/{asset_id}/materializations", response_model=list[AssetMaterializationEntry])
async def asset_materializations(
    asset_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[AssetMaterializationEntry]:
    asset = await _resolve_or_404(session, workspace_id=ctx.workspace.id, asset_id=asset_id)
    rows = await AssetRepository(session).materializations(asset_id=asset.id)
    return [AssetMaterializationEntry.model_validate(r) for r in rows]
