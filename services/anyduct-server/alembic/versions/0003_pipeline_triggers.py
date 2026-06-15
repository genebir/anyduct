"""pipeline_triggers table — call-pipeline downstream triggers (ADR-0029)

Revision ID: 0003_pipeline_triggers
Revises: 0002_cursors
Create Date: 2026-05-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_pipeline_triggers"
down_revision: str | None = "0002_cursors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipeline_triggers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_pipeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_pipeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["source_pipeline_id"], ["pipelines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_pipeline_id"], ["pipelines.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("source_pipeline_id", "target_pipeline_id", name="uq_pipeline_trigger"),
    )
    op.create_index(
        "ix_pipeline_triggers_source_pipeline_id",
        "pipeline_triggers",
        ["source_pipeline_id"],
    )
    op.create_index(
        "ix_pipeline_triggers_target_pipeline_id",
        "pipeline_triggers",
        ["target_pipeline_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_triggers_target_pipeline_id", table_name="pipeline_triggers")
    op.drop_index("ix_pipeline_triggers_source_pipeline_id", table_name="pipeline_triggers")
    op.drop_table("pipeline_triggers")
