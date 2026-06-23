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

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from anyduct_server.db.models import (
    Asset,
    AssetColumn,
    AssetEdge,
    AssetMaterialization,
    ColumnLineageEdge,
)
from etl_plugins.core.asset import AssetKey, AssetLineage
from etl_plugins.core.column_lineage import ColumnEdge, ColumnLineage, ColumnRef

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
        asset = (
            await self._session.execute(
                select(Asset).where(Asset.workspace_id == workspace_id, Asset.asset_key == str(key))
            )
        ).scalar_one_or_none()
        if asset is not None:
            return asset
        # Case-insensitive fallback (ADR-0099 dogfood): sqlglot lowercases
        # unquoted identifiers, so a column-lineage upstream key
        # ``conn/bda_ds.t`` won't exact-match the table-level asset
        # ``conn/BDA_DS.T`` (uppercase, as written) — the edge would be
        # silently dropped. Only runs when the exact match fails, so
        # case-matching callers are unaffected.
        return (
            (
                await self._session.execute(
                    select(Asset).where(
                        Asset.workspace_id == workspace_id,
                        func.lower(Asset.asset_key) == str(key).lower(),
                    )
                )
            )
            .scalars()
            .first()
        )

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
        # Case-insensitive fallback — reuse an existing column that differs
        # only in case (sqlglot-lowercased upstream column vs the real
        # uppercase one) instead of creating a duplicate.
        ci = (
            (
                await self._session.execute(
                    select(AssetColumn).where(
                        AssetColumn.asset_id == asset_id,
                        func.lower(AssetColumn.name) == name.lower(),
                    )
                )
            )
            .scalars()
            .first()
        )
        if ci is not None:
            return ci
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

    async def column_lineage_graph(
        self, *, asset_id: UUID, max_depth: int, max_assets: int = 40, direction: str = "upstream"
    ) -> tuple[
        dict[UUID, tuple[Asset, int]],
        dict[UUID, list[str]],
        list[tuple[UUID, str, UUID, str]],
        bool,
    ]:
        """Multi-hop upstream column-lineage subgraph (2026-06-12).

        BFS from ``asset_id`` walking ``column_lineage_edges`` upstream up
        to ``max_depth`` hops, capped at ``max_assets`` total assets so a
        wide warehouse can't explode the response. Returns:

        * ``assets``  — ``{asset_id: (Asset, depth)}``; depth 0 = the root,
          1 = direct upstream, … (an asset reachable at several depths is
          recorded at its SHALLOWEST — that's also its render lane).
        * ``columns`` — ``{asset_id: sorted column names}`` (recorded
          ``asset_columns`` union names touched by collected edges, so pure
          source assets that never persisted a column set still show the
          columns that feed downstream).
        * ``edges``   — ``(up_asset_id, up_col, down_asset_id, down_col)``.
        * ``truncated`` — true when the asset cap cut the walk OR more
          hops exist beyond ``max_depth``.
        """
        root = await self._session.get(Asset, asset_id)
        if root is None:
            return {}, {}, [], False
        depths: dict[UUID, tuple[Asset, int]] = {asset_id: (root, 0)}
        frontier: set[UUID] = {asset_id}
        edges: list[tuple[UUID, str, UUID, str]] = []
        seen_edges: set[tuple[UUID, str, UUID, str]] = set()
        truncated = False

        down_col = aliased(AssetColumn)
        up_col = aliased(AssetColumn)
        # ``downstream`` walks impact (this column → who consumes it): the
        # frontier is the UPSTREAM side and we reach DOWNSTREAM columns. The
        # default ``upstream`` walk is the provenance drill-down (the reverse).
        is_down = direction == "downstream"
        boundary_col = up_col if is_down else down_col  # frontier side of an edge
        reach_col = down_col if is_down else up_col  # newly discovered side
        for depth in range(1, max_depth + 1):
            if not frontier:
                break
            rows = await self._session.execute(
                select(
                    up_col.asset_id,
                    up_col.name,
                    down_col.asset_id,
                    down_col.name,
                    Asset,
                )
                .select_from(ColumnLineageEdge)
                .join(down_col, down_col.id == ColumnLineageEdge.downstream_column_id)
                .join(up_col, up_col.id == ColumnLineageEdge.upstream_column_id)
                .join(Asset, Asset.id == reach_col.asset_id)
                .where(boundary_col.asset_id.in_(frontier))
            )
            next_frontier: set[UUID] = set()
            for up_aid, up_name, dn_aid, dn_name, reach_asset in rows.all():
                reach_aid = dn_aid if is_down else up_aid
                key = (up_aid, up_name, dn_aid, dn_name)
                if reach_aid not in depths:
                    if len(depths) >= max_assets:
                        truncated = True
                        continue
                    depths[reach_aid] = (reach_asset, depth)
                    next_frontier.add(reach_aid)
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append(key)
            frontier = next_frontier

        if frontier:
            # Depth cap reached with unexplored assets — does anything continue
            # past them? One existence probe so the UI can say "more …".
            probe = await self._session.execute(
                select(ColumnLineageEdge.id)
                .join(down_col, down_col.id == ColumnLineageEdge.downstream_column_id)
                .join(up_col, up_col.id == ColumnLineageEdge.upstream_column_id)
                .where(boundary_col.asset_id.in_(frontier))
                .limit(1)
            )
            if probe.first() is not None:
                truncated = True

        cols_result = await self._session.execute(
            select(AssetColumn.asset_id, AssetColumn.name).where(
                AssetColumn.asset_id.in_(depths.keys())
            )
        )
        columns: dict[UUID, set[str]] = {aid: set() for aid in depths}
        for aid, name in cols_result.all():
            columns[aid].add(name)
        for up_aid, up_name, dn_aid, dn_name in edges:
            columns[up_aid].add(up_name)
            columns[dn_aid].add(dn_name)
        return (
            depths,
            {aid: sorted(names) for aid, names in columns.items()},
            edges,
            truncated,
        )

    async def asset_lineage_graph(
        self, *, asset_id: UUID, max_depth: int, max_assets: int = 60
    ) -> tuple[dict[UUID, tuple[Asset, int]], list[tuple[UUID, UUID]], bool]:
        """Multi-hop TABLE-level lineage subgraph, both directions
        (2026-06-12). BFS over ``asset_edges`` from ``asset_id``:
        upstream hops get NEGATIVE depths (rendered left of the root),
        downstream hops positive. Capped at ``max_assets`` total;
        ``truncated`` is true when the cap cut the walk or more hops
        exist beyond ``max_depth`` in either direction.
        """
        root = await self._session.get(Asset, asset_id)
        if root is None:
            return {}, [], False
        depths: dict[UUID, tuple[Asset, int]] = {asset_id: (root, 0)}
        edges: set[tuple[UUID, UUID]] = set()
        truncated = False

        async def walk(direction: int) -> None:
            """direction -1 = upstream (follow edges backwards), +1 = downstream."""
            nonlocal truncated
            frontier: set[UUID] = {asset_id}
            for hop in range(1, max_depth + 1):
                if not frontier:
                    return
                if direction < 0:
                    stmt = (
                        select(AssetEdge.upstream_asset_id, AssetEdge.downstream_asset_id, Asset)
                        .join(Asset, Asset.id == AssetEdge.upstream_asset_id)
                        .where(AssetEdge.downstream_asset_id.in_(frontier))
                    )
                else:
                    stmt = (
                        select(AssetEdge.upstream_asset_id, AssetEdge.downstream_asset_id, Asset)
                        .join(Asset, Asset.id == AssetEdge.downstream_asset_id)
                        .where(AssetEdge.upstream_asset_id.in_(frontier))
                    )
                rows = await self._session.execute(stmt)
                next_frontier: set[UUID] = set()
                for up_id, down_id, neighbor in rows.all():
                    new_id = up_id if direction < 0 else down_id
                    if new_id not in depths:
                        if len(depths) >= max_assets:
                            truncated = True
                            continue
                        depths[new_id] = (neighbor, direction * hop)
                        next_frontier.add(new_id)
                    edges.add((up_id, down_id))
                frontier = next_frontier
            if frontier:
                # Depth cap hit with unexplored assets — anything beyond?
                if direction < 0:
                    probe_stmt = (
                        select(AssetEdge.id)
                        .where(AssetEdge.downstream_asset_id.in_(frontier))
                        .limit(1)
                    )
                else:
                    probe_stmt = (
                        select(AssetEdge.id)
                        .where(AssetEdge.upstream_asset_id.in_(frontier))
                        .limit(1)
                    )
                probe = await self._session.execute(probe_stmt)
                if probe.first() is not None:
                    truncated = True

        await walk(-1)
        await walk(+1)
        # Keep only edges whose BOTH ends made it into the subgraph (an
        # edge to a cap-dropped asset would dangle).
        kept = [(u, d) for u, d in edges if u in depths and d in depths]
        return depths, kept, truncated


__all__ = ["AssetRepository", "ColumnEdge", "ColumnLineage", "ColumnRef"]
