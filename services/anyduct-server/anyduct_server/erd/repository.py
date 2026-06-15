"""ErdDiagramRepository — async DB access for ``erd_diagrams`` (Phase AHD).

Mutations stay in the caller's transaction (no commit inside) so the router
can pair each write with an audit row and commit them as a unit — same
contract as the other repositories.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.db.models import ErdDiagram


class ErdDiagramRepository:
    """Async data access for ``erd_diagrams``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_workspace(self, *, workspace_id: UUID) -> list[ErdDiagram]:
        result = await self._session.execute(
            select(ErdDiagram)
            .where(ErdDiagram.workspace_id == workspace_id)
            .order_by(ErdDiagram.updated_at.desc())
        )
        return list(result.scalars().all())

    async def get(self, *, workspace_id: UUID, diagram_id: UUID) -> ErdDiagram | None:
        result = await self._session.execute(
            select(ErdDiagram).where(
                ErdDiagram.workspace_id == workspace_id,
                ErdDiagram.id == diagram_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        workspace_id: UUID,
        name: str,
        design_json: dict[str, Any],
        created_by_user_id: UUID | None,
    ) -> ErdDiagram:
        row = ErdDiagram(
            workspace_id=workspace_id,
            name=name,
            design_json=design_json,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def update(
        self,
        diagram: ErdDiagram,
        *,
        name: str | None = None,
        design_json: dict[str, Any] | None = None,
    ) -> ErdDiagram:
        if name is not None:
            diagram.name = name
        if design_json is not None:
            diagram.design_json = design_json
        await self._session.flush()
        return diagram

    async def delete(self, diagram: ErdDiagram) -> None:
        await self._session.delete(diagram)
        await self._session.flush()
