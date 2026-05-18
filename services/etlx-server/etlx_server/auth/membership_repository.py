"""Membership table access (Step 8.3, ADR-0023).

Reads only — admin-driven membership CRUD lives in Step 8.5 with its own
mutation surface. Used by :func:`workspace_context.get_current_workspace` to
resolve the role a given user holds in a given workspace.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.db.enums import WorkspaceRole
from etlx_server.db.models import Membership


class MembershipRepository:
    """Async read access for ``memberships``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_role(self, *, workspace_id: UUID, user_id: UUID) -> WorkspaceRole | None:
        """Return the user's role in the workspace, or ``None`` if not a member."""
        result = await self._session.execute(
            select(Membership.role).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()


__all__ = ["MembershipRepository"]
