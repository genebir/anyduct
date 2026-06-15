"""ErdDiagram — a saved entity-relationship diagram (Phase AHD, ADR-0090).

A workspace-scoped, server-persisted ERD so diagrams are durable + shared
across users/devices (like pipelines/connections), not browser-local. The
``design_json`` is the opaque designer model (tables + relations) the web
ERD designer reads/writes — the server doesn't interpret it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from anyduct_server.db.base import Base, TimestampMixin, UUIDMixin


class ErdDiagram(UUIDMixin, TimestampMixin, Base):
    """One saved ERD diagram: ``name`` → ``design_json`` (tables + relations)."""

    __tablename__ = "erd_diagrams"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Opaque designer model: {"tables": [...], "relations": [...]}.
    design_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
