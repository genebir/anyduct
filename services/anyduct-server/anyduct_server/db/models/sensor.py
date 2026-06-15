"""Sensor model (ADR-0041 K3b) — external-event triggers.

See ``alembic/versions/0008_sensors.py`` for the schema rationale. The
sensor scheduler polls active rows on their ``poll_interval_seconds``
cadence, invokes :func:`etl_plugins.core.sensor.build_sensor` to run
the configured ``check()``, and on ``triggered=True`` enqueues a
PENDING Run of ``target_pipeline_id``.

``last_*`` columns are pure cache: every poll updates ``last_check_at``
(+ ``last_result_json``), and a successful trigger also bumps
``last_triggered_at``. The UI surfaces these without re-running the
check.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from anyduct_server.db.base import Base, TimestampMixin, UUIDMixin


class Sensor(UUIDMixin, TimestampMixin, Base):
    """One external-event trigger configuration."""

    __tablename__ = "sensors"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_sensor_ws_name"),
        Index("ix_sensors_workspace", "workspace_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Dispatch key for ``etl_plugins.core.sensor.build_sensor``.
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Pipeline that runs on a positive check. NULL ⇒ sensor orphaned (target
    # pipeline was deleted) — scheduler skips + logs instead of crashing.
    target_pipeline_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pipelines.id", ondelete="SET NULL"), nullable=True
    )
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
