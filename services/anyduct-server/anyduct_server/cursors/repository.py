"""Async repository for the ``cursors`` table.

Mutations stay in the caller's transaction (no commit inside the repo) so
the worker can pair a cursor update with the corresponding Run row write
and commit them as one unit. The repo only knows about the ORM model;
the higher-level :class:`DbCursorState` adapter wraps it to satisfy the
core :class:`etl_plugins.core.cursor.CursorState` ABC.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.db.models import Cursor


class CursorRepository:
    """Async data access for ``cursors``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, *, workspace_id: UUID, name: str) -> Cursor | None:
        result = await self._session.execute(
            select(Cursor).where(
                Cursor.workspace_id == workspace_id,
                Cursor.name == name,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_workspace(self, *, workspace_id: UUID) -> list[Cursor]:
        result = await self._session.execute(
            select(Cursor).where(Cursor.workspace_id == workspace_id).order_by(Cursor.name)
        )
        return list(result.scalars().all())

    async def upsert(
        self,
        *,
        workspace_id: UUID,
        name: str,
        cursor_column: str,
        cursor_value: Any,
    ) -> Cursor:
        """Insert or update by ``(workspace_id, name)``.

        Uses PostgreSQL's ``INSERT ... ON CONFLICT DO UPDATE`` so the call
        is atomic + race-safe even when multiple workers checkpoint the
        same key (last-write-wins, ordered by the DB).
        """
        stmt = (
            insert(Cursor)
            .values(
                workspace_id=workspace_id,
                name=name,
                cursor_column=cursor_column,
                cursor_value=cursor_value,
            )
            .on_conflict_do_update(
                index_elements=["workspace_id", "name"],
                set_={"cursor_column": cursor_column, "cursor_value": cursor_value},
            )
            .returning(Cursor)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def delete(self, *, workspace_id: UUID, name: str) -> bool:
        """Remove a cursor row. Returns True if a row was deleted."""
        result = await self._session.execute(
            delete(Cursor).where(
                Cursor.workspace_id == workspace_id,
                Cursor.name == name,
            )
        )
        # ``rowcount`` is on the underlying CursorResult; sqlalchemy.Result
        # exposes it but mypy stubs only on CursorResult — cast away.
        rowcount: int = getattr(result, "rowcount", 0) or 0
        return rowcount > 0
