"""node_runs — node-level execution queue (ADR-0041, Phase H1)

Revision ID: 0005_node_runs
Revises: 0004_assets_lineage
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_node_runs"
down_revision: str | None = "0004_assets_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Reuse the existing run_status enum (created in 0001); do not re-create it.
_run_status = postgresql.ENUM(name="run_status", create_type=False)


def upgrade() -> None:
    op.create_table(
        "node_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", _run_status, nullable=False, server_default="pending"),
        sa.Column(
            "depends_on",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("pending_deps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=255), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_class", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("output_ref", sa.Text(), nullable=True),
        sa.Column(
            "result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "node_id", name="uq_node_runs_run_node"),
    )
    op.create_index("ix_node_runs_claim", "node_runs", ["status", "pending_deps"])
    op.create_index("ix_node_runs_run", "node_runs", ["run_id"])
    op.create_index("ix_node_runs_heartbeat", "node_runs", ["heartbeat_at"])
    op.create_index("ix_node_runs_depends_on", "node_runs", ["depends_on"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_node_runs_depends_on", table_name="node_runs")
    op.drop_index("ix_node_runs_heartbeat", table_name="node_runs")
    op.drop_index("ix_node_runs_run", table_name="node_runs")
    op.drop_index("ix_node_runs_claim", table_name="node_runs")
    op.drop_table("node_runs")
