"""Audit log. ADR-0023.

모든 mutating endpoint가 한 행 남긴다. ``actor`` / ``workspace`` / ``action``
/ ``resource_type`` / ``resource_id`` / ``before_json`` / ``after_json`` /
``ip`` / ``user_agent`` / ``created_at``.

SuperAdmin이 다른 워크스페이스에 접근하면 그 워크스페이스의 audit_log에 row가
남고, ``actor_user_id`` 의 ``is_superadmin``과 함께 검색에 사용된다.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from etlx_server.db.base import Base, UUIDMixin


class AuditLog(UUIDMixin, Base):
    """One immutable audit row per mutating action."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_workspace_created", "workspace_id", "created_at"),
        Index("ix_audit_actor", "actor_user_id", "created_at"),
        Index("ix_audit_resource", "resource_type", "resource_id"),
    )

    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    # 자유 형식 action 이름 (예: "connection.create", "pipeline.update", "run.cancel").
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
