"""cursors table for DB-backed CursorState (Step 6.1)

Revision ID: 0002_cursors
Revises: 0001_initial
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_cursors"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cursors",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("cursor_column", sa.String(length=255), nullable=False),
        # JSONB so any CursorValue (int/float/str/bool/datetime-as-ISO) survives
        # a round trip without a separate type-discriminator column.
        sa.Column("cursor_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "name"),
    )


def downgrade() -> None:
    op.drop_table("cursors")
