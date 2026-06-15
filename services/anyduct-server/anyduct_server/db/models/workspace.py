"""Workspace / User / Membership / PersonalAccessToken. ADR-0023.

도메인 격리: 모든 비즈니스 리소스는 Workspace에 속한다 (`workspace_id` FK).
사용자는 다중 워크스페이스 멤버일 수 있으며, 워크스페이스마다 역할이 다를 수 있다.
글로벌 운영자(SuperAdmin)는 ``users.is_superadmin`` 컬럼으로 표현.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from anyduct_server.db.base import Base, TimestampMixin, UUIDMixin
from anyduct_server.db.enums import AuthMethod, WorkspaceRole


class Workspace(UUIDMixin, TimestampMixin, Base):
    """A workspace = 비즈니스 격리 단위. 모든 도메인 리소스가 여기 속한다."""

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Arc-style 좌측 4px 컬러 바 (DESIGN.md §7.4). 자유 색상 — UI가 검증.
    color_hex: Mapped[str] = mapped_column(String(9), nullable=False, default="#FF3D8B")

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class User(UUIDMixin, TimestampMixin, Base):
    """End user — OIDC SSO 또는 로컬 fallback (email+bcrypt)."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_method: Mapped[AuthMethod] = mapped_column(
        PG_ENUM(AuthMethod, name="auth_method", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=AuthMethod.LOCAL,
    )
    # ``auth_method=local`` 일 때만 채움. OIDC 사용자는 NULL.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Membership(UUIDMixin, TimestampMixin, Base):
    """User x Workspace x Role. (workspace_id, user_id) 유일."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_membership_ws_user"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        PG_ENUM(
            WorkspaceRole, name="workspace_role", values_callable=lambda e: [m.value for m in e]
        ),
        nullable=False,
    )

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class PersonalAccessToken(UUIDMixin, TimestampMixin, Base):
    """API용 PAT (`anyduct_pat_*`). 사용자가 UI에서 발급/회수. ADR-0023 §4."""

    __tablename__ = "personal_access_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # `anyduct_pat_<8자 prefix>` — UI에 표시. 검색 시 prefix로 매칭.
    prefix: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    # 전체 토큰의 bcrypt(또는 sha256) 해시. 평문은 발급 시점에만 사용자에게 노출.
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
