"""sensors table (ADR-0041 K3b)

Stores external-event triggers: a sensor polls its configured check
(`type` + `config_json`) on its `poll_interval_seconds` cadence, and
when the check returns ``triggered=True`` the ``SensorScheduler``
enqueues a PENDING Run of ``target_pipeline_id``.

* ``last_check_at`` ticks on every poll (triggered or not) so the
  scheduler can compute "next due" without a JOIN through runs.
* ``last_triggered_at`` only ticks when the check fired; useful for the
  UI ("last fired N minutes ago") and for de-duplication policies we
  may add later.
* ``last_result_json`` carries the last :class:`SensorResult` dump
  (``triggered``/``message``/``metadata``) so operators can debug
  quietly-failing sensors from the catalog endpoint without re-checking.

Revision ID: 0008_sensors
Revises: 0007_column_lineage
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_sensors"
down_revision: str | None = "0007_column_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sensors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        # Sensor type (e.g. ``http``). Dispatched via
        # ``etl_plugins.core.sensor.build_sensor(type, config_json)``.
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column(
            "config_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # Pipeline to trigger on positive check. SET NULL so deleting the
        # pipeline doesn't cascade-delete useful sensor history; the
        # scheduler treats target=NULL as "sensor orphaned, skip + log".
        sa.Column("target_pipeline_id", postgresql.UUID(as_uuid=True), nullable=True),
        # How often to poll, in seconds. Floored to 5s at the scheduler
        # tick to avoid hammering downstreams on a typo.
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
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
        sa.ForeignKeyConstraint(["target_pipeline_id"], ["pipelines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_sensor_ws_name"),
    )
    op.create_index("ix_sensors_workspace", "sensors", ["workspace_id"])
    # Active sensors are the only ones the scheduler scans; partial index
    # keeps the tick query cheap even with many disabled sensors.
    op.create_index(
        "ix_sensors_active_due",
        "sensors",
        ["last_check_at"],
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_sensors_active_due", table_name="sensors")
    op.drop_index("ix_sensors_workspace", table_name="sensors")
    op.drop_table("sensors")
