"""Asset / lineage persistence (ADR-0024 / ADR-0036, Phase B).

Mirrors the core ``etl_plugins.core.asset`` model in the metadata DB so the
catalog + lineage graph survive across runs and can be served over REST.

* :class:`Asset` — one data asset per ``(workspace, asset_key)``. ``asset_key``
  is the rendered core ``AssetKey`` (``"connection/target"``), workspace-scoped.
* :class:`AssetEdge` — a directed ``upstream → downstream`` lineage dependency.
* :class:`AssetMaterialization` — a record that a run wrote an output asset
  (time-series; ``run_id`` SET NULL on run delete so history survives).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
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
