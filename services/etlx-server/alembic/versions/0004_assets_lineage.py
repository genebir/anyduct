"""assets / asset_edges / asset_materializations — lineage persistence (ADR-0036)

Revision ID: 0004_assets_lineage
Revises: 0003_pipeline_triggers
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_assets_lineage"
down_revision: str | None = "0003_pipeline_triggers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_key", sa.String(length=1024), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=True),
        sa.Column("last_materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", "asset_key", name="uq_asset_ws_key"),
    )
    op.create_index("ix_assets_workspace", "assets", ["workspace_id"])

    op.create_table(
        "asset_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("upstream_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("downstream_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["upstream_asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["downstream_asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("upstream_asset_id", "downstream_asset_id", name="uq_asset_edge"),
    )
    op.create_index("ix_asset_edges_workspace", "asset_edges", ["workspace_id"])
    op.create_index("ix_asset_edges_downstream", "asset_edges", ["downstream_asset_id"])

    op.create_table(
        "asset_materializations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("records_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "materialized_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_asset_mat_asset_time", "asset_materializations", ["asset_id", "materialized_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_asset_mat_asset_time", table_name="asset_materializations")
    op.drop_table("asset_materializations")
    op.drop_index("ix_asset_edges_downstream", table_name="asset_edges")
    op.drop_index("ix_asset_edges_workspace", table_name="asset_edges")
    op.drop_table("asset_edges")
    op.drop_index("ix_assets_workspace", table_name="assets")
    op.drop_table("assets")
