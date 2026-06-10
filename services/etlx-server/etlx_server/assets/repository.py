"""AssetRepository — persist + query the asset/lineage graph (ADR-0036, Phase B;
ADR-0041 J2 adds column-level lineage).

The worker calls :meth:`persist_run_lineage` after a successful run to record
the assets it touched (derived-first, from the core ``AssetLineage``), their
``input → output`` edges, and a materialization row per output, then
:meth:`persist_run_column_lineage` to record per-column wiring derived by
``etl_plugins.runtime.derive_column_lineage``. The catalog endpoints
(B3 + J2) read through :meth:`list_for_workspace` / :meth:`lineage` /
:meth:`materializations` / :meth:`column_lineage_for_asset`.

Assets are workspace-scoped and keyed by the rendered core ``AssetKey``
(``"connection/target"``). Upserts are idempotent so re-running a pipeline
doesn't duplicate assets or edges — only a new materialization row is added.
Column lineage uses **replace semantics per asset**: each successful run
overwrites the column set + edges of its output assets, so the row set
always reflects the latest materialization. Input-side columns are left
alone (other pipelines may own them).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.core.asset import AssetKey, AssetLineage
from etl_plugins.core.column_lineage import ColumnEdge, ColumnLineage, ColumnRef
from etlx_server.db.models import (
    Asset,
    AssetColumn,
    AssetEdge,
    AssetMaterialization,
    ColumnLineageEdge,
)

_DEFAULT_LIMIT = 200
_MAX_LIMIT = 1000


class AssetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------- write (worker) --------------------------------------------

    async def _upsert_asset(self, workspace_id: UUID, key: AssetKey, kind: str | None) -> Asset:
        rendered = str(key)
        # Race-safe get-or-create. Multi-replica workers (ADR-0021 queue)
        # can finish runs touching the same asset concurrently; a plain
        # select-then-insert double-inserts and trips ``uq_asset_ws_key``
        # (caught by the multi-worker e2e). ``ON CONFLICT DO NOTHING``
        # makes the insert atomic; the follow-up select sees either our
        # row or the winner's.
        await self._session.execute(
            pg_insert(Asset)
            .values(workspace_id=workspace_id, asset_key=rendered, kind=kind)
            .on_conflict_do_nothing(constraint="uq_asset_ws_key")
        )
        existing = (
            await self._session.execute(
                select(Asset).where(Asset.workspace_id == workspace_id, Asset.asset_key == rendered)
            )
        ).scalar_one()
        # Backfill kind if we learned it later; never clobber a known kind.
        if kind and not existing.kind:
            existing.kind = kind
        return existing

    async def _upsert_edge(self, workspace_id: UUID, upstream: Asset, downstream: Asset) -> None:
        if upstream.id == downstream.id:
            return
        # Same race-safety story as ``_upsert_asset`` (``uq_asset_edge``).
        await self._session.execute(
            pg_insert(AssetEdge)
            .values(
                workspace_id=workspace_id,
                upstream_asset_id=upstream.id,
                downstream_asset_id=downstream.id,
            )
            .on_conflict_do_nothing(constraint="uq_asset_edge")
        )

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

    # ---------- write: column lineage (J2) --------------------------------

    async def _asset_by_key(self, workspace_id: UUID, key: AssetKey) -> Asset | None:
        return (
            await self._session.execute(
                select(Asset).where(Asset.workspace_id == workspace_id, Asset.asset_key == str(key))
            )
        ).scalar_one_or_none()

    async def _ensure_column(self, asset_id: UUID, name: str) -> AssetColumn:
        existing = (
            await self._session.execute(
                select(AssetColumn).where(
                    AssetColumn.asset_id == asset_id, AssetColumn.name == name
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        col = AssetColumn(asset_id=asset_id, name=name)
        self._session.add(col)
        await self._session.flush()
        return col

    async def _ensure_edge(
        self, workspace_id: UUID, downstream: AssetColumn, upstream: AssetColumn
    ) -> None:
        if downstream.id == upstream.id:
            return
        existing = (
            await self._session.execute(
                select(ColumnLineageEdge).where(
                    ColumnLineageEdge.downstream_column_id == downstream.id,
                    ColumnLineageEdge.upstream_column_id == upstream.id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        self._session.add(
            ColumnLineageEdge(
                workspace_id=workspace_id,
                downstream_column_id=downstream.id,
                upstream_column_id=upstream.id,
            )
        )
        await self._session.flush()

    async def persist_run_column_lineage(
        self,
        *,
        workspace_id: UUID,
        lineage: ColumnLineage,
        output_keys: list[AssetKey],
    ) -> None:
        """Persist the static column-level lineage produced by
        :func:`etl_plugins.runtime.derive_column_lineage`.

        Replace-per-output-asset semantics: every output asset's columns and
        outgoing column edges are wiped first, then re-inserted from the
        fresh derivation. Input-side columns (assets that are *only* read)
        are left alone — another pipeline may own them.

        The asset rows themselves must already exist; call this *after*
        :meth:`persist_run_lineage`. Caller commits. Best-effort — the worker
        wraps this in try/except so a column-lineage glitch never flips a
        successful run to failed.
        """
        opaque_set = {str(k) for k in lineage.opaque_assets}
        out_set = {str(k) for k in output_keys}

        # Flip the opaque flag on each output asset + clear its derived
        # column set (CASCADE deletes downstream edges; upstream edges feeding
        # *into* its columns also disappear because columns themselves are
        # cascade-deleted).
        for key in output_keys:
            asset = await self._asset_by_key(workspace_id, key)
            if asset is None:
                continue
            asset.column_lineage_opaque = str(key) in opaque_set
            await self._session.execute(delete(AssetColumn).where(AssetColumn.asset_id == asset.id))
        await self._session.flush()

        # Re-insert columns + edges. We tolerate edges whose downstream is
        # *not* an output of this run (shouldn't happen for derived lineage,
        # but cheap to handle) by upserting the downstream asset's column
        # row too.
        col_cache: dict[tuple[UUID, str], AssetColumn] = {}

        async def _col(asset: Asset, name: str) -> AssetColumn:
            cache_key = (asset.id, name)
            if cache_key in col_cache:
                return col_cache[cache_key]
            col = await self._ensure_column(asset.id, name)
            col_cache[cache_key] = col
            return col

        def _is_known_output(ref: ColumnRef) -> bool:
            return str(ref.asset) in out_set

        for edge in lineage.edges:
            if not _is_known_output(edge.downstream):
                # Defensive: don't fan column rows onto assets we didn't
                # ask to record this turn.
                continue
            ds_asset = await self._asset_by_key(workspace_id, edge.downstream.asset)
            if ds_asset is None:
                continue
            ds_col = await _col(ds_asset, edge.downstream.column)
            for up in edge.upstreams:
                up_asset = await self._asset_by_key(workspace_id, up.asset)
                if up_asset is None:
                    continue
                up_col = await _col(up_asset, up.column)
                await self._ensure_edge(workspace_id, ds_col, up_col)
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

    async def column_lineage_for_asset(
        self, *, asset_id: UUID
    ) -> tuple[list[AssetColumn], dict[UUID, list[tuple[AssetColumn, Asset]]]]:
        """Return (columns of this asset, upstream map per column).

        ``upstream map``: ``{downstream_column_id: [(upstream_column, upstream_asset), …]}``.
        Columns with no upstream edges (e.g. ``add_constant``) appear in
        the columns list with an empty entry in the map.
        """
        cols_result = await self._session.execute(
            select(AssetColumn).where(AssetColumn.asset_id == asset_id).order_by(AssetColumn.name)
        )
        columns = list(cols_result.scalars().all())
        if not columns:
            return columns, {}

        col_ids = [c.id for c in columns]
        edges_result = await self._session.execute(
            select(ColumnLineageEdge, AssetColumn, Asset)
            .join(AssetColumn, AssetColumn.id == ColumnLineageEdge.upstream_column_id)
            .join(Asset, Asset.id == AssetColumn.asset_id)
            .where(ColumnLineageEdge.downstream_column_id.in_(col_ids))
            .order_by(Asset.asset_key, AssetColumn.name)
        )
        upstream_map: dict[UUID, list[tuple[AssetColumn, Asset]]] = {c.id: [] for c in columns}
        for edge, up_col, up_asset in edges_result.all():
            upstream_map[edge.downstream_column_id].append((up_col, up_asset))
        return columns, upstream_map


__all__ = ["AssetRepository", "ColumnEdge", "ColumnLineage", "ColumnRef"]
