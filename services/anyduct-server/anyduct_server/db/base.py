"""Declarative base + 공통 mixin들. ADR-0020.

* :class:`Base` — SQLAlchemy 2.x ``DeclarativeBase``.
* :class:`UUIDMixin` — uuid7 PK 컬럼.
* :class:`TimestampMixin` — ``created_at`` / ``updated_at`` 자동 stamping.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from anyduct_server.db.uuid7 import uuid7


class Base(DeclarativeBase):
    """Project-wide declarative base. 한 곳에 metadata를 모아 Alembic이 검출."""


class UUIDMixin:
    """uuid7 PK."""

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )


class TimestampMixin:
    """``created_at`` / ``updated_at`` 자동 stamping. server_default + onupdate."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
