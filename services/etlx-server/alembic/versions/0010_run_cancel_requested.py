"""runs.cancel_requested_at column (Phase P, 2026-05-28 user-requested cancel)

Stamped by ``POST /workspaces/{ws}/runs/{rid}/cancel`` to request
cancellation of an in-flight run. The worker's heartbeat loop polls
this column each tick and, when set, signals a threading.Event the
node-level graph executor checks between waves — yielding cooperative
cancellation without touching the ``status`` column from the API side
(the worker stays the single writer for run status, avoiding race
conditions between API "CANCELLED" and worker "SUCCEEDED").

No index needed — the column is only read in the heartbeat loop's
already-keyed single-row UPDATE-RETURNING; no scans hit it.

Revision ID: 0010_run_cancel_requested
Revises: 0009_run_logs_node_id
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_run_cancel_requested"
down_revision: str | None = "0009_run_logs_node_id"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "cancel_requested_at")
