"""asset_columns + column_lineage_edges + assets.column_lineage_opaque (ADR-0041 J2)

Persistence for the static column-level lineage derived in core
(``etl_plugins.runtime.derive_column_lineage``). One row per column we
know exists on a sink asset (``asset_columns``), plus directed edges
between a downstream column and its upstream column(s)
(``column_lineage_edges``, n→1). A new ``assets.column_lineage_opaque``
flag marks assets whose column mapping is undecidable (python transforms,
``SELECT *``, joins, …) so the UI can distinguish "no edges yet" from
"derived as opaque".

Revision ID: 0007_column_lineage
Revises: 0006_workspace_variables
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_column_lineage"
down_revision: str | None = "0006_workspace_variables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column(
            "column_lineage_opaque",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "asset_columns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("asset_id", "name", name="uq_asset_column"),
    )
    op.create_index("ix_asset_columns_asset", "asset_columns", ["asset_id"])

    op.create_table(
        "column_lineage_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("downstream_column_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("upstream_column_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["downstream_column_id"], ["asset_columns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["upstream_column_id"], ["asset_columns.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("downstream_column_id", "upstream_column_id", name="uq_column_edge"),
    )
    op.create_index("ix_column_edges_workspace", "column_lineage_edges", ["workspace_id"])
    op.create_index("ix_column_edges_downstream", "column_lineage_edges", ["downstream_column_id"])
    op.create_index("ix_column_edges_upstream", "column_lineage_edges", ["upstream_column_id"])


def downgrade() -> None:
    op.drop_index("ix_column_edges_upstream", table_name="column_lineage_edges")
    op.drop_index("ix_column_edges_downstream", table_name="column_lineage_edges")
    op.drop_index("ix_column_edges_workspace", table_name="column_lineage_edges")
    op.drop_table("column_lineage_edges")
    op.drop_index("ix_asset_columns_asset", table_name="asset_columns")
    op.drop_table("asset_columns")
    op.drop_column("assets", "column_lineage_opaque")
