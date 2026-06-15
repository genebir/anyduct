"""Membership table access (Step 8.3 read; Step 8.5b CRUD).

Read paths feed the RBAC dependency chain
(:func:`workspace_context.get_current_workspace`); mutating paths back the
membership router. As with the workspace repo, mutations participate in
the caller's transaction — the router pairs each one with an audit row +
``session.commit`` so the two land atomically.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.db.enums import WorkspaceRole
from anyduct_server.db.models import Membership, User


class MembershipExistsError(Exception):
    """Raised when ``add`` would violate the (workspace_id, user_id) UNIQUE."""


class LastOwnerError(Exception):
    """Raised when a mutation would leave a workspace without any Owner.

    Workspaces with no Owner are unmanageable (no one can add members,
    rename, or delete), so the repository refuses to demote or remove
    the last Owner. The router lifts this into a 409.
    """


class MembershipRepository:
    """Async access for ``memberships``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- read ----------------------------------------------------------

    async def get_role(self, *, workspace_id: UUID, user_id: UUID) -> WorkspaceRole | None:
        """Return the user's role in the workspace, or ``None`` if not a member."""
        result = await self._session.execute(
            select(Membership.role).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get(self, *, workspace_id: UUID, user_id: UUID) -> Membership | None:
        """Return the full membership row (or ``None``)."""
        result = await self._session.execute(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_workspace(self, *, workspace_id: UUID) -> list[tuple[Membership, User]]:
        """Return ``(Membership, User)`` rows for FE display, sorted by user email."""
        stmt = (
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.workspace_id == workspace_id)
            .order_by(User.email)
        )
        result = await self._session.execute(stmt)
        return [(m, u) for m, u in result.all()]

    async def count_owners(self, *, workspace_id: UUID) -> int:
        """Count Owner memberships in a workspace."""
        stmt = select(func.count()).where(
            Membership.workspace_id == workspace_id,
            Membership.role == WorkspaceRole.OWNER,
        )
        return int((await self._session.execute(stmt)).scalar_one())

    # --- mutations -----------------------------------------------------

    async def add(self, *, workspace_id: UUID, user_id: UUID, role: WorkspaceRole) -> Membership:
        """Create one membership row. Raises :class:`MembershipExistsError` on dup."""
        row = Membership(workspace_id=workspace_id, user_id=user_id, role=role)
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as e:
            await self._session.rollback()
            raise MembershipExistsError(
                f"user {user_id} is already a member of workspace {workspace_id}"
            ) from e
        return row

    async def update_role(self, membership: Membership, new_role: WorkspaceRole) -> Membership:
        """Change role; raises :class:`LastOwnerError` if demoting the only Owner."""
        if (
            membership.role is WorkspaceRole.OWNER
            and new_role is not WorkspaceRole.OWNER
            and await self.count_owners(workspace_id=membership.workspace_id) <= 1
        ):
            raise LastOwnerError("cannot demote the last Owner of a workspace")
        membership.role = new_role
        await self._session.flush()
        return membership

    async def remove(self, membership: Membership) -> None:
        """Delete; raises :class:`LastOwnerError` if removing the only Owner."""
        if (
            membership.role is WorkspaceRole.OWNER
            and await self.count_owners(workspace_id=membership.workspace_id) <= 1
        ):
            raise LastOwnerError("cannot remove the last Owner of a workspace")
        await self._session.delete(membership)
        await self._session.flush()


__all__ = [
    "LastOwnerError",
    "MembershipExistsError",
    "MembershipRepository",
]
