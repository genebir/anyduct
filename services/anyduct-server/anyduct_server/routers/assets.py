"""Data catalog: assets + lineage (ADR-0036, Phase B3; ADR-0041 J2).

| Method | Path                                                       | Auth    |
|--------|------------------------------------------------------------|---------|
| GET    | ``/workspaces/{ws}/assets``                                | Viewer+ |
| GET    | ``/workspaces/{ws}/assets/{id}/lineage``                   | Viewer+ |
| GET    | ``/workspaces/{ws}/assets/{id}/materializations``          | Viewer+ |
| GET    | ``/workspaces/{ws}/assets/{id}/column-lineage`` (J2)       | Viewer+ |

Read-only — the worker is the only writer (Phase B2 / J2). Assets are
addressed by their DB id (uuid), not the ``"conn/target"`` key, so the
slash in the key doesn't fight the URL path.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.assets.repository import AssetRepository
from anyduct_server.auth.schemas import (
    AssetColumnEntry,
    AssetColumnLineageGraphResponse,
    AssetColumnLineageResponse,
    AssetLineageGraphResponse,
    AssetLineageResponse,
    AssetMaterializationEntry,
    AssetRef,
    AssetSummary,
    ColumnGraphAssetEntry,
    ColumnGraphEdgeEntry,
    ColumnUpstreamRef,
    LineageGraphAssetEntry,
    LineageGraphEdgeEntry,
)
from anyduct_server.auth.workspace_context import WorkspaceContext, require_workspace_role
from anyduct_server.db.enums import WorkspaceRole
from anyduct_server.db.models import Asset
from anyduct_server.dependencies import get_session

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


@router.get("/{asset_id}/column-lineage", response_model=AssetColumnLineageResponse)
async def asset_column_lineage(
    asset_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> AssetColumnLineageResponse:
    """Per-column lineage drill-down (ADR-0041 J2).

    Returns one entry per column known to exist on this asset (alphabetical),
    each with the upstream column refs that feed it. ``opaque=true`` means
    the worker derived this asset's column mapping as untraceable; the UI
    typically renders a badge instead of the column list.
    """
    asset = await _resolve_or_404(session, workspace_id=ctx.workspace.id, asset_id=asset_id)
    columns, upstream_map = await AssetRepository(session).column_lineage_for_asset(
        asset_id=asset.id
    )
    entries = [
        AssetColumnEntry(
            name=c.name,
            upstreams=[
                ColumnUpstreamRef(
                    asset_id=up_asset.id, asset_key=up_asset.asset_key, column=up_col.name
                )
                for up_col, up_asset in upstream_map.get(c.id, [])
            ],
        )
        for c in columns
    ]
    return AssetColumnLineageResponse(
        id=asset.id,
        asset_key=asset.asset_key,
        opaque=asset.column_lineage_opaque,
        columns=entries,
    )


@router.get("/{asset_id}/lineage-graph", response_model=AssetLineageGraphResponse)
async def asset_lineage_graph(
    asset_id: UUID,
    depth: int = 3,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> AssetLineageGraphResponse:
    """Multi-hop TABLE-level lineage, both directions (2026-06-12).

    BFS over ``asset_edges`` up to ``depth`` hops each way (clamped to
    [1, 5]), at most 60 assets. Upstream hops carry negative depths
    (rendered left of the root), downstream positive — the conventional
    catalog lineage DAG. Supersedes the 1-hop ``/lineage`` for the
    detail view; that endpoint stays for compatibility.
    """
    depth = max(1, min(depth, 5))
    asset = await _resolve_or_404(session, workspace_id=ctx.workspace.id, asset_id=asset_id)
    assets, edges, truncated = await AssetRepository(session).asset_lineage_graph(
        asset_id=asset.id, max_depth=depth
    )
    return AssetLineageGraphResponse(
        id=asset.id,
        asset_key=asset.asset_key,
        max_depth=depth,
        truncated=truncated,
        assets=[
            LineageGraphAssetEntry(id=aid, asset_key=a.asset_key, kind=a.kind, depth=d)
            for aid, (a, d) in sorted(assets.items(), key=lambda kv: (kv[1][1], kv[1][0].asset_key))
        ],
        edges=[LineageGraphEdgeEntry(from_asset_id=u, to_asset_id=d) for u, d in edges],
    )


@router.get("/{asset_id}/column-lineage-graph", response_model=AssetColumnLineageGraphResponse)
async def asset_column_lineage_graph(
    asset_id: UUID,
    depth: int = 3,
    direction: str = "upstream",
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> AssetColumnLineageGraphResponse:
    """Multi-hop column lineage (2026-06-12; ``direction`` added 2026-06-19).

    ``direction=upstream`` (default) is the provenance drill-down — BFS over
    ``column_lineage_edges`` toward the columns that FEED the root.
    ``direction=downstream`` is **column impact analysis** — which downstream
    columns CONSUME the root's columns ("what breaks if I change this column").
    BFS up to ``depth`` hops (clamped [1, 5]), ≤40 assets. ``opaque`` refers to
    the ROOT asset."""
    depth = max(1, min(depth, 5))
    direction = "downstream" if direction == "downstream" else "upstream"
    asset = await _resolve_or_404(session, workspace_id=ctx.workspace.id, asset_id=asset_id)
    assets, columns, edges, truncated = await AssetRepository(session).column_lineage_graph(
        asset_id=asset.id, max_depth=depth, direction=direction
    )
    return AssetColumnLineageGraphResponse(
        id=asset.id,
        asset_key=asset.asset_key,
        opaque=asset.column_lineage_opaque,
        max_depth=depth,
        truncated=truncated,
        assets=[
            ColumnGraphAssetEntry(
                id=aid,
                asset_key=a.asset_key,
                depth=d,
                columns=columns.get(aid, []),
            )
            for aid, (a, d) in sorted(assets.items(), key=lambda kv: (kv[1][1], kv[1][0].asset_key))
        ],
        edges=[
            ColumnGraphEdgeEntry(
                from_asset_id=up_aid,
                from_column=up_col,
                to_asset_id=dn_aid,
                to_column=dn_col,
            )
            for up_aid, up_col, dn_aid, dn_col in edges
        ],
    )
