"""run_logs.node_id column (Phase M, 2026-05-26 user request 'NODE 컬럼')

Adds an optional ``node_id`` column to ``run_logs`` so per-node logs
(node-level execution, ADR-0041 H2) can be filtered in the run-detail
UI without parsing ``context_json``. Backfill is not needed — old rows
keep ``node_id IS NULL`` (treated as "run-level" by the UI), new rows
get the value from structlog's :func:`merge_contextvars` processor
once the worker binds ``node_id`` around each node execution.

Index ``(run_id, node_id, ts)`` lets the UI's filter-by-node query
(``WHERE run_id=? AND node_id=? ORDER BY ts``) hit a single index range
scan even on long-running graph jobs. Kept partial-style by *including*
the run_id leading column so the existing run-wide log query still uses
the original ``ix_run_logs_run_ts`` index unchanged.

Revision ID: 0009_run_logs_node_id
Revises: 0008_sensors
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_run_logs_node_id"
down_revision: str | None = "0008_sensors"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_logs",
        sa.Column("node_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_run_logs_run_node_ts",
        "run_logs",
        ["run_id", "node_id", "ts"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_logs_run_node_ts", table_name="run_logs")
    op.drop_column("run_logs", "node_id")
