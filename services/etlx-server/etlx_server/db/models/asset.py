"""Asset / lineage persistence (ADR-0024 / ADR-0036, Phase B; ADR-0041 J2).

Mirrors the core ``etl_plugins.core.asset`` model in the metadata DB so the
catalog + lineage graph survive across runs and can be served over REST.

* :class:`Asset` — one data asset per ``(workspace, asset_key)``. ``asset_key``
  is the rendered core ``AssetKey`` (``"connection/target"``), workspace-scoped.
  ``column_lineage_opaque`` (J2) marks assets whose column mapping is
  undecidable (python transform, ``SELECT *``, join, …); persisted so the UI
  can distinguish "no edges yet" from "derived as opaque".
* :class:`AssetEdge` — a directed ``upstream → downstream`` lineage dependency.
* :class:`AssetMaterialization` — a record that a run wrote an output asset
  (time-series; ``run_id`` SET NULL on run delete so history survives).
* :class:`AssetColumn` (J2) — one row per column we know exists on an asset.
  Unique per ``(asset_id, name)``.
* :class:`ColumnLineageEdge` (J2) — directed edge between a downstream column
  and one upstream column. Multiple upstreams per downstream = multiple rows
  (n→1).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from etlx_server.db.base import Base, TimestampMixin, UUIDMixin


class Asset(UUIDMixin, TimestampMixin, Base):
    """A data asset a pipeline reads or writes, workspace-scoped."""

    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "asset_key", name="uq_asset_ws_key"),
        Index("ix_assets_workspace", "workspace_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # Rendered core AssetKey, e.g. "warehouse/public.orders".
    asset_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_materialized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ADR-0041 J2: true ⇒ derive_column_lineage gave up on this asset
    # (python transform / SELECT * / join / direct table source). The UI
    # shows an "opaque" badge instead of a column drill-down.
    column_lineage_opaque: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )


class AssetEdge(UUIDMixin, TimestampMixin, Base):
    """Directed lineage dependency: ``upstream`` feeds ``downstream``."""

    __tablename__ = "asset_edges"
    __table_args__ = (
        UniqueConstraint("upstream_asset_id", "downstream_asset_id", name="uq_asset_edge"),
        Index("ix_asset_edges_workspace", "workspace_id"),
        Index("ix_asset_edges_downstream", "downstream_asset_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    upstream_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    downstream_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )


class AssetMaterialization(UUIDMixin, Base):
    """A run wrote an output asset. Time-series (no ``updated_at``)."""

    __tablename__ = "asset_materializations"
    __table_args__ = (Index("ix_asset_mat_asset_time", "asset_id", "materialized_at"),)

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL (not CASCADE) so the materialization history survives run pruning.
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    records_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    materialized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class AssetColumn(UUIDMixin, TimestampMixin, Base):
    """One column known to exist on an asset (ADR-0041 J2).

    Populated by the worker after a successful run from the core
    ``ColumnLineage`` derivation. Re-running a pipeline replaces the column
    set for its output assets (delete-then-insert per asset), so the row set
    always reflects the most recent successful materialization.
    """

    __tablename__ = "asset_columns"
    __table_args__ = (
        UniqueConstraint("asset_id", "name", name="uq_asset_column"),
        Index("ix_asset_columns_asset", "asset_id"),
    )

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class ColumnLineageEdge(UUIDMixin, TimestampMixin, Base):
    """A single upstream-column → downstream-column dependency (ADR-0041 J2).

    n upstream columns feeding one downstream = n rows. Edges live in their
    own table (not on ``AssetColumn``) so we can issue a single workspace-
    scoped query when serving the drill-down view.
    """

    __tablename__ = "column_lineage_edges"
    __table_args__ = (
        UniqueConstraint("downstream_column_id", "upstream_column_id", name="uq_column_edge"),
        Index("ix_column_edges_workspace", "workspace_id"),
        Index("ix_column_edges_downstream", "downstream_column_id"),
        Index("ix_column_edges_upstream", "upstream_column_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    downstream_column_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_columns.id", ondelete="CASCADE"), nullable=False
    )
    upstream_column_id: Mapped[UUID] = mapped_column(
        ForeignKey("asset_columns.id", ondelete="CASCADE"), nullable=False
    )
