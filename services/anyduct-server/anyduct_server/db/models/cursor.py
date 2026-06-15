"""Cursor watermark — DB-backed CursorState (Step 6.1 / ADR-0024).

Each row is one watermark for one logical sync within a workspace. The
key is a stable string the caller chooses (e.g.
``"pipeline:<pipeline_id>:task:<task_id>"``); ``cursor_value`` is JSONB
so it round-trips ``int`` / ``str`` / ``float`` / ``bool`` / ISO-8601
``datetime`` strings without a value-type column.

Composite PK on ``(workspace_id, name)`` — the same key may exist in
different workspaces without collision. ``ON DELETE CASCADE`` from
workspaces, so deleting a workspace cleans up its cursors too.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from anyduct_server.db.base import Base


class Cursor(Base):
    """Watermark row keyed by ``(workspace_id, name)``."""

    __tablename__ = "cursors"

    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String(255), primary_key=True)

    cursor_column: Mapped[str] = mapped_column(String(255), nullable=False)
    # JSONB so any CursorValue (int/float/str/bool/datetime-as-ISO) survives a
    # round trip without a separate type-discriminator column. ``None`` means
    # "no progress yet" — the runtime treats it as "read from the beginning".
    cursor_value: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # No created_at — first write *is* the create.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
