"""WorkspaceVariable — workspace-wide global pipeline variables (ADR-0041, V2).

A named, non-secret config value referenced in pipeline configs as
``${var.name}`` (see :mod:`etl_plugins.config.variables`). Globals are merged
*under* a pipeline's local ``variables`` block at build time (locals win).

Variables are **not** secrets — they're stored in plaintext. Sensitive values
belong in the secret backend (``${SECRET:...}``), not here.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from etlx_server.db.base import Base, TimestampMixin, UUIDMixin


class WorkspaceVariable(UUIDMixin, TimestampMixin, Base):
    """One workspace-global variable: ``name`` → JSON ``value``."""

    __tablename__ = "workspace_variables"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_workspace_variables_ws_name"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # Referenceable as ``${var.<name>}`` — an identifier (validated at the API).
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Any JSON value (string / number / bool / list / dict). Non-secret.
    value_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
