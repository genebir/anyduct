"""Connection — 코어의 ``ConnectionConfig``가 DB에 저장된 형태. ADR-0020.

핵심 규칙(ADR-0017 §6 + ADR-0020 §6):
  * ``config_json``에는 **시크릿 평문 절대 저장 금지**. 값은 `${SECRET_REF}` 형식의
    placeholder만 들어가고 실제 값은 외부 secret backend(Vault / AWS SM / GCP SM /
    파일)가 보관. 어플리케이션 레벨이 보장한다 — DB는 단순 JSONB.
  * ``secret_refs``는 placeholder 키들의 목록 (예: ``["PG_PASSWORD"]``)이고
    UI/API가 검증 + secret backend lookup에 사용.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from etlx_server.db.base import Base, TimestampMixin, UUIDMixin


class Connection(UUIDMixin, TimestampMixin, Base):
    """One named connection within a workspace."""

    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_connection_ws_name"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Connector type — 코어의 ``ConnectorRegistry`` 키 (postgres / mysql / sqlite / s3 / kafka / ...)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # ``etl_plugins.config.models.ConnectionConfig`` dump. secret placeholders only.
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # 코드 검사용 secret reference 키 목록 (예: ["PG_PASSWORD", "DW_TOKEN"]).
    secret_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
