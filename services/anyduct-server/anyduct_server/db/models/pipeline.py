"""Pipeline / PipelineVersion / Schedule.

Pipeline = 사용자가 만든 ETL 정의. 변경 이력은 ``PipelineVersion``으로 관리
(immutable snapshot 모델). Schedule은 Pipeline에 대한 트리거 정책 (cron 또는
stream-active).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from anyduct_server.db.base import Base, TimestampMixin, UUIDMixin
from anyduct_server.db.enums import PipelineMode


class Pipeline(UUIDMixin, TimestampMixin, Base):
    """Logical pipeline. 실제 동작 정의는 ``PipelineVersion``들이 보관."""

    __tablename__ = "pipelines"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_pipeline_ws_name"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    versions: Mapped[list[PipelineVersion]] = relationship(
        back_populates="pipeline",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PipelineVersion.version",
    )


class PipelineVersion(UUIDMixin, TimestampMixin, Base):
    """Immutable snapshot of a pipeline's config. ``version``은 1부터 증가."""

    __tablename__ = "pipeline_versions"
    __table_args__ = (UniqueConstraint("pipeline_id", "version", name="uq_pipeline_version_num"),)

    pipeline_id: Mapped[UUID] = mapped_column(
        ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # ``etl_plugins.config.models.PipelineConfig`` 전체 dump (mode/source/transforms/sink/retry/dlq/...).
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # 가장 최근 활성 버전 한 개만 True. 응용 레벨로 보장 (트랜잭션).
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    pipeline: Mapped[Pipeline] = relationship(back_populates="versions")


class PipelineTrigger(UUIDMixin, TimestampMixin, Base):
    """Downstream pipeline to trigger on success (ADR-0029).

    A directed edge: when ``source_pipeline`` finishes a run successfully, the
    worker enqueues a run of ``target_pipeline``. Fire-and-forget for v1 — the
    source does not wait for the target. Cycles are broken at run time via the
    run's ``result_json.trigger_chain``.
    """

    __tablename__ = "pipeline_triggers"
    __table_args__ = (
        UniqueConstraint("source_pipeline_id", "target_pipeline_id", name="uq_pipeline_trigger"),
    )

    source_pipeline_id: Mapped[UUID] = mapped_column(
        ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_pipeline_id: Mapped[UUID] = mapped_column(
        ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )


class Schedule(UUIDMixin, TimestampMixin, Base):
    """Trigger policy attached to a pipeline."""

    __tablename__ = "schedules"

    pipeline_id: Mapped[UUID] = mapped_column(
        ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # cron 표현식 (UTC). croniter로 평가. mode=stream인 경우 NULL 가능 (계속 활성).
    cron_expr: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mode: Mapped[PipelineMode] = mapped_column(
        PG_ENUM(PipelineMode, name="pipeline_mode", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # PipelineVersion.config_json 위에 덧씌울 부분 (예: connections override, cursor_from 등).
    config_overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
