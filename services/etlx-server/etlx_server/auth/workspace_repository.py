"""Workspace table access — read-only loader used by the RBAC dependency chain.

Workspace CRUD (create, rename, delete, list) is part of Step 8.5 and will
extend this repository there.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.db.models import Workspace


class WorkspaceRepository:
    """Async read access for ``workspaces``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, workspace_id: UUID) -> Workspace | None:
        result = await self._session.execute(select(Workspace).where(Workspace.id == workspace_id))
        return result.scalar_one_or_none()


__all__ = ["WorkspaceRepository"]
