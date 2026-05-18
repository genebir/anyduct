"""ConnectionRepository — async DB access for ``connections`` (Step 8.5c).

Mutations stay in the caller's transaction (no commit inside the repo) so
the router can pair each write with secret-backend writes + an audit row
and commit them as one unit.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.db.models import Connection


class ConnectionNameTakenError(Exception):
    """Raised when ``create`` / ``update`` collides with an existing name in the workspace."""


_ALLOWED_UPDATE_FIELDS = frozenset({"name", "type", "config_json", "secret_refs"})


class ConnectionRepository:
    """Async data access for ``connections``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- read ----------------------------------------------------------

    async def list_for_workspace(self, *, workspace_id: UUID) -> list[Connection]:
        result = await self._session.execute(
            select(Connection)
            .where(Connection.workspace_id == workspace_id)
            .order_by(Connection.name)
        )
        return list(result.scalars().all())

    async def get(self, *, workspace_id: UUID, connection_id: UUID) -> Connection | None:
        result = await self._session.execute(
            select(Connection).where(
                Connection.workspace_id == workspace_id,
                Connection.id == connection_id,
            )
        )
        return result.scalar_one_or_none()

    # --- mutations -----------------------------------------------------

    async def add(
        self,
        *,
        connection_id: UUID,
        workspace_id: UUID,
        name: str,
        type: str,
        config_json: dict[str, Any],
        secret_refs: list[str],
        created_by_user_id: UUID | None,
    ) -> Connection:
        """Insert one row with a pre-minted UUID (so secret paths can be
        computed before the backend writes)."""
        row = Connection(
            id=connection_id,
            workspace_id=workspace_id,
            name=name,
            type=type,
            config_json=config_json,
            secret_refs=secret_refs,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as e:
            await self._session.rollback()
            raise ConnectionNameTakenError(
                f"connection name {name!r} is already taken in this workspace"
            ) from e
        return row

    async def update(self, connection: Connection, /, **fields: Any) -> Connection:
        """Apply a whitelist of fields. Name collisions surface as 409."""
        unknown = set(fields) - _ALLOWED_UPDATE_FIELDS
        if unknown:
            raise ValueError(f"unknown connection update fields: {sorted(unknown)}")
        for key, value in fields.items():
            setattr(connection, key, value)
        try:
            await self._session.flush()
        except IntegrityError as e:
            await self._session.rollback()
            raise ConnectionNameTakenError("connection name is already taken") from e
        return connection

    async def delete(self, connection: Connection) -> None:
        await self._session.delete(connection)
        await self._session.flush()

    @staticmethod
    def snapshot(connection: Connection) -> dict[str, Any]:
        """Compact JSON-safe view of a connection — used for audit before/after."""
        return {
            "name": connection.name,
            "type": connection.type,
            "config_json": connection.config_json,
            "secret_refs": list(connection.secret_refs),
        }


__all__ = ["ConnectionNameTakenError", "ConnectionRepository"]
