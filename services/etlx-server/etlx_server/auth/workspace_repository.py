"""Workspace table access — read + admin CRUD.

The read path (`get_by_id`, `list_for_user`) is shared with the RBAC
dependency chain. The mutating path (`create`, `update`, `delete`) backs
the Step 8.5a router; each mutation participates in the caller's
transaction (no commit inside the repo) so the router can pair it with
audit recording atomically.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.db.enums import WorkspaceRole
from etlx_server.db.models import Membership, Workspace


class WorkspaceSlugTakenError(Exception):
    """Raised when ``create`` / ``update`` would collide with an existing slug."""


_ALLOWED_UPDATE_FIELDS = frozenset({"name", "slug", "color_hex"})


class WorkspaceRepository:
    """Async data access for ``workspaces``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- read ----------------------------------------------------------

    async def get_by_id(self, workspace_id: UUID) -> Workspace | None:
        result = await self._session.execute(select(Workspace).where(Workspace.id == workspace_id))
        return result.scalar_one_or_none()

    async def list_for_user(self, *, user_id: UUID, include_all: bool = False) -> list[Workspace]:
        """Workspaces the user belongs to, ordered by name.

        ``include_all=True`` (used for SuperAdmin) skips the membership
        join and returns every workspace.
        """
        stmt = select(Workspace)
        if not include_all:
            stmt = stmt.join(Membership, Membership.workspace_id == Workspace.id).where(
                Membership.user_id == user_id
            )
        stmt = stmt.order_by(Workspace.name)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # --- mutations -----------------------------------------------------

    async def create(
        self,
        *,
        name: str,
        slug: str,
        color_hex: str,
        owner_user_id: UUID,
    ) -> Workspace:
        """Insert a workspace + Owner membership in one transaction.

        Slug uniqueness is enforced at the DB level — collisions surface
        as :class:`WorkspaceSlugTakenError` so the router can return 409
        without leaking SQLAlchemy details.
        """
        workspace = Workspace(name=name, slug=slug, color_hex=color_hex)
        self._session.add(workspace)
        try:
            await self._session.flush()
        except IntegrityError as e:
            await self._session.rollback()
            raise WorkspaceSlugTakenError(f"slug {slug!r} is already taken") from e

        self._session.add(
            Membership(
                workspace_id=workspace.id,
                user_id=owner_user_id,
                role=WorkspaceRole.OWNER,
            )
        )
        await self._session.flush()
        return workspace

    async def update(self, workspace: Workspace, /, **fields: Any) -> Workspace:
        """Apply a subset of ``name``/``slug``/``color_hex`` to ``workspace``.

        Unknown fields raise ``ValueError`` — keeps random kwargs from
        the request body wandering into the model. Slug collisions
        surface as :class:`WorkspaceSlugTakenError`.
        """
        unknown = set(fields) - _ALLOWED_UPDATE_FIELDS
        if unknown:
            raise ValueError(f"unknown workspace update fields: {sorted(unknown)}")
        for key, value in fields.items():
            setattr(workspace, key, value)
        try:
            await self._session.flush()
        except IntegrityError as e:
            await self._session.rollback()
            raise WorkspaceSlugTakenError("slug is already taken") from e
        return workspace

    async def delete(self, workspace: Workspace) -> None:
        """Delete a workspace. FK cascade removes memberships."""
        await self._session.delete(workspace)
        await self._session.flush()

    @staticmethod
    def snapshot(workspace: Workspace, fields: Iterable[str] = ()) -> dict[str, Any]:
        """Return a JSON-safe dict of selected fields — handy for audit ``before``/``after``."""
        keys = tuple(fields) or ("name", "slug", "color_hex")
        return {k: getattr(workspace, k) for k in keys}


__all__ = ["WorkspaceRepository", "WorkspaceSlugTakenError"]
