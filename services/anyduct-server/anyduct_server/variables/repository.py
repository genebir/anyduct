"""WorkspaceVariableRepository — async DB access for ``workspace_variables``.

Mutations stay in the caller's transaction (no commit inside the repo) so the
router can pair each write with an audit row and commit them as a unit.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.db.models import WorkspaceVariable


class WorkspaceVariableRepository:
    """Async data access for ``workspace_variables``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_workspace(self, *, workspace_id: UUID) -> list[WorkspaceVariable]:
        result = await self._session.execute(
            select(WorkspaceVariable)
            .where(WorkspaceVariable.workspace_id == workspace_id)
            .order_by(WorkspaceVariable.name)
        )
        return list(result.scalars().all())

    async def get(self, *, workspace_id: UUID, name: str) -> WorkspaceVariable | None:
        result = await self._session.execute(
            select(WorkspaceVariable).where(
                WorkspaceVariable.workspace_id == workspace_id,
                WorkspaceVariable.name == name,
            )
        )
        return result.scalar_one_or_none()

    async def set(
        self,
        *,
        workspace_id: UUID,
        name: str,
        value: Any,
        description: str | None,
    ) -> tuple[WorkspaceVariable, bool]:
        """Upsert by (workspace, name). Returns ``(row, created)``."""
        existing = await self.get(workspace_id=workspace_id, name=name)
        if existing is not None:
            existing.value_json = value
            existing.description = description
            await self._session.flush()
            return existing, False
        row = WorkspaceVariable(
            workspace_id=workspace_id, name=name, value_json=value, description=description
        )
        self._session.add(row)
        await self._session.flush()
        return row, True

    async def delete(self, variable: WorkspaceVariable) -> None:
        await self._session.delete(variable)
        await self._session.flush()

    async def as_dict(self, *, workspace_id: UUID) -> dict[str, Any]:
        """``{name: value}`` for variable resolution at pipeline build time."""
        rows = await self.list_for_workspace(workspace_id=workspace_id)
        return {r.name: r.value_json for r in rows}


__all__ = ["WorkspaceVariableRepository"]
