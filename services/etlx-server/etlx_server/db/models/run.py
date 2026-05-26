"""Run / RunLog / RunMetric. ADR-0021.

``runs`` 테이블은 도메인 SSOT 이자 **워커 큐** 역할을 동시에 한다. 워커는
``status='pending' AND scheduled_at <= now()`` 행을 ``FOR UPDATE SKIP LOCKED``
로 잡아 ``running``으로 바꾸고 코어의 ``run_pipeline_yaml``을 호출한다.

heartbeat_at은 워커가 ~30s마다 갱신. 만료된 ``running`` row는 다른 워커가
zombie로 회수해서 다시 ``pending``으로 되돌리거나 ``failed`` 처리한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from etlx_server.db.base import Base, TimestampMixin, UUIDMixin
from etlx_server.db.enums import LogLevel, RunStatus


class Run(UUIDMixin, TimestampMixin, Base):
    """One execution attempt of a pipeline version. 큐 + 결과 모두 보관."""

    __tablename__ = "runs"
    __table_args__ = (
        # 워커 큐 폴링 핫패스: pending + scheduled_at 정렬. partial index 후보 (Alembic에서).
        Index("ix_runs_queue_poll", "status", "scheduled_at"),
        # 워크스페이스 단위 조회.
        Index("ix_runs_workspace_created", "workspace_id", "created_at"),
        # 좀비 회수: heartbeat 만료 row 검색.
        Index("ix_runs_heartbeat", "heartbeat_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_id: Mapped[UUID] = mapped_column(
        ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("pipeline_versions.id", ondelete="RESTRICT"), nullable=False
    )
    # 트리거 출처: cron schedule 또는 수동 (NULL).
    schedule_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True
    )
    triggered_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[RunStatus] = mapped_column(
        PG_ENUM(RunStatus, name="run_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=RunStatus.PENDING,
    )
    # 큐 대기 시점 — 이 시각 이후 워커가 claim 가능. 즉시 실행은 now().
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # 코어 RunResult 매핑.
    records_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 임의 추가 결과 (예: 코어 ``RunResult.run_id``, OpenLineage event id, etc.)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    logs: Mapped[list[RunLog]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    metrics: Mapped[list[RunMetric]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RunLog(UUIDMixin, Base):
    """Structured log line attached to a run. 시계열 — created_at 없이 ts 직접.

    ``node_id`` (Phase M, 2026-05-26 user request) is populated for
    logs emitted during node-level execution (ADR-0041 H2): the worker
    binds ``node_id`` into a structlog ContextVar around each node, and
    :func:`merge_contextvars` injects it into every event dict. Logs
    with ``node_id IS NULL`` are run-level (pre-node-execution: build,
    connectors, post-run summary, …). The UI uses this to filter the
    log panel to one node when the user clicks a node in the run DAG.
    """

    __tablename__ = "run_logs"
    __table_args__ = (
        Index("ix_run_logs_run_ts", "run_id", "ts"),
        # Filter-by-node lookup; leading run_id keeps run-wide queries
        # off this second index so we don't double the write cost.
        Index("ix_run_logs_run_node_ts", "run_id", "node_id", "ts"),
    )

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    level: Mapped[LogLevel] = mapped_column(
        PG_ENUM(LogLevel, name="log_level", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=LogLevel.INFO,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL = run-level log; non-NULL = emitted while a specific graph
    # node was executing. Bound by the worker via structlog ContextVar.
    node_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # structlog event_dict 등 추가 컨텍스트.
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    run: Mapped[Run] = relationship(back_populates="logs")


class RunMetric(UUIDMixin, Base):
    """Metric point emitted during a run (코어 metrics ABC를 DB-backed로 export)."""

    __tablename__ = "run_metrics"
    __table_args__ = (Index("ix_run_metrics_run_name", "run_id", "name"),)

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    # 코어의 표준 이름들 (RECORDS_READ_TOTAL, DURATION_SECONDS, ERRORS_TOTAL, ...)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    attrs_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    run: Mapped[Run] = relationship(back_populates="metrics")
