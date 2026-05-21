"""AssetRepository — persist + query the asset/lineage graph (ADR-0036, Phase B).

The worker calls :meth:`persist_run_lineage` after a successful run to record
the assets it touched (derived-first, from the core ``AssetLineage``), their
``input → output`` edges, and a materialization row per output. The catalog
endpoints (B3) read through :meth:`list_for_workspace` / :meth:`lineage` /
:meth:`materializations`.

Assets are workspace-scoped and keyed by the rendered core ``AssetKey``
(``"connection/target"``). Upserts are idempotent so re-running a pipeline
doesn't duplicate assets or edges — only a new materialization row is added.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.core.asset import AssetKey, AssetLineage
from etlx_server.db.models import Asset, AssetEdge, AssetMaterialization

_DEFAULT_LIMIT = 200
_MAX_LIMIT = 1000


class AssetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------- write (worker) --------------------------------------------

    async def _upsert_asset(self, workspace_id: UUID, key: AssetKey, kind: str | None) -> Asset:
        rendered = str(key)
        existing = (
            await self._session.execute(
                select(Asset).where(Asset.workspace_id == workspace_id, Asset.asset_key == rendered)
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Backfill kind if we learned it later; never clobber a known kind.
            if kind and not existing.kind:
                existing.kind = kind
            return existing
        asset = Asset(workspace_id=workspace_id, asset_key=rendered, kind=kind)
        self._session.add(asset)
        await self._session.flush()
        return asset

    async def _upsert_edge(self, workspace_id: UUID, upstream: Asset, downstream: Asset) -> None:
        if upstream.id == downstream.id:
            return
        existing = (
            await self._session.execute(
                select(AssetEdge).where(
                    AssetEdge.upstream_asset_id == upstream.id,
                    AssetEdge.downstream_asset_id == downstream.id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        self._session.add(
            AssetEdge(
                workspace_id=workspace_id,
                upstream_asset_id=upstream.id,
                downstream_asset_id=downstream.id,
            )
        )
        await self._session.flush()

    async def persist_run_lineage(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID | None,
        lineage: AssetLineage,
        records_written: int,
        kinds: dict[AssetKey, str | None] | None = None,
    ) -> None:
        """Idempotently upsert the run's assets + edges, and add one
        materialization per output asset. Caller commits."""
        kinds = kinds or {}
        rows: dict[AssetKey, Asset] = {}
        for key in (*lineage.inputs, *lineage.outputs):
            if key not in rows:
                rows[key] = await self._upsert_asset(workspace_id, key, kinds.get(key))

        for edge in lineage.edges:
            up = rows.get(edge.upstream)
            down = rows.get(edge.downstream)
            if up is not None and down is not None:
                await self._upsert_edge(workspace_id, up, down)

        now = datetime.now(UTC)
        for key in lineage.outputs:
            asset = rows[key]
            self._session.add(
                AssetMaterialization(
                    asset_id=asset.id,
                    run_id=run_id,
                    records_written=records_written,
                    materialized_at=now,
                )
            )
            asset.last_materialized_at = now
        await self._session.flush()

    # ---------- read (catalog API) ----------------------------------------

    async def list_for_workspace(self, *, workspace_id: UUID) -> list[Asset]:
        result = await self._session.execute(
            select(Asset).where(Asset.workspace_id == workspace_id).order_by(Asset.asset_key)
        )
        return list(result.scalars().all())

    async def get(self, *, workspace_id: UUID, asset_id: UUID) -> Asset | None:
        return (
            await self._session.execute(
                select(Asset).where(Asset.id == asset_id, Asset.workspace_id == workspace_id)
            )
        ).scalar_one_or_none()

    async def upstream(self, asset_id: UUID) -> list[Asset]:
        result = await self._session.execute(
            select(Asset)
            .join(AssetEdge, AssetEdge.upstream_asset_id == Asset.id)
            .where(AssetEdge.downstream_asset_id == asset_id)
            .order_by(Asset.asset_key)
        )
        return list(result.scalars().all())

    async def downstream(self, asset_id: UUID) -> list[Asset]:
        result = await self._session.execute(
            select(Asset)
            .join(AssetEdge, AssetEdge.downstream_asset_id == Asset.id)
            .where(AssetEdge.upstream_asset_id == asset_id)
            .order_by(Asset.asset_key)
        )
        return list(result.scalars().all())

    async def materializations(
        self, *, asset_id: UUID, limit: int = _DEFAULT_LIMIT
    ) -> list[AssetMaterialization]:
        limit = max(1, min(limit, _MAX_LIMIT))
        result = await self._session.execute(
            select(AssetMaterialization)
            .where(AssetMaterialization.asset_id == asset_id)
            .order_by(AssetMaterialization.materialized_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


__all__ = ["AssetRepository"]
