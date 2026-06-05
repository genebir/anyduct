"""erd_diagrams table (Phase AHD, ADR-0090)

Server-persisted, workspace-scoped ERD diagrams so they're durable and
shared across users/devices (previously browser localStorage only). The
``design_json`` is the opaque designer model (tables + relations).

Revision ID: 0011_erd_diagrams
Revises: 0010_run_cancel_requested
Create Date: 2026-06-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_erd_diagrams"
down_revision: str | None = "0010_run_cancel_requested"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "erd_diagrams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "design_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_erd_diagrams_workspace", "erd_diagrams", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_erd_diagrams_workspace", table_name="erd_diagrams")
    op.drop_table("erd_diagrams")
