"""NodeRun — node-level execution queue + tracking (ADR-0041, Phase H).

A pipeline ``Run`` (one execution of a graph) expands into one ``node_run`` per
graph node. Nodes form a DAG via ``depends_on``; a node becomes claimable when
all its upstreams have succeeded. Workers claim ready nodes with
``FOR UPDATE SKIP LOCKED`` so independent branches run in parallel across
workers — the same Postgres-queue pattern as ``runs`` (ADR-0021), one level down.

Readiness uses a **dependency counter**: ``pending_deps`` starts at
``len(depends_on)`` and is decremented when an upstream succeeds (matched by the
``depends_on @> [node_id]`` JSONB containment). ``pending_deps = 0`` ⇒ ready.
``output_ref`` points at where the node materialized its output (the staging
hand-off mechanism is decided in H2); H1 only tracks lifecycle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from anyduct_server.db.base import Base, TimestampMixin, UUIDMixin
from anyduct_server.db.enums import RunStatus


class NodeRun(UUIDMixin, TimestampMixin, Base):
    """One node's execution within a pipeline run. 노드 단위 큐 + 결과."""

    __tablename__ = "node_runs"
    __table_args__ = (
        # run 내 node_id 유일.
        UniqueConstraint("run_id", "node_id", name="uq_node_runs_run_node"),
        # 노드 큐 폴링 핫패스: ready(pending + pending_deps=0) 검색.
        Index("ix_node_runs_claim", "status", "pending_deps"),
        # run 단위 진행/조회.
        Index("ix_node_runs_run", "run_id"),
        # 좀비 회수: heartbeat 만료 row.
        Index("ix_node_runs_heartbeat", "heartbeat_at"),
        # upstream 완료 시 하류 decrement: depends_on @> [node_id] containment.
        Index("ix_node_runs_depends_on", "depends_on", postgresql_using="gin"),
    )

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    # 그래프 노드 id (PipelineVersion.config_json의 graph node id).
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # source | transform | sink | join | aggregate — 관측/디버깅용.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[RunStatus] = mapped_column(
        PG_ENUM(RunStatus, name="run_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=RunStatus.PENDING,
    )
    # 직속 상류 노드 id들. 하류 readiness 카운터(pending_deps)와 함께 사용.
    depends_on: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # 아직 succeeded 안 된 상류 수. 0 ⇒ claim 가능. 상류 succeeded 시 1씩 감소.
    pending_deps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    records_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_class: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 노드가 출력을 materialize한 위치(staging 핸드오프). 메커니즘은 H2에서 결정 —
    # H1에선 nullable 포인터로만 둔다 (예: "conn/staging_table").
    output_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
