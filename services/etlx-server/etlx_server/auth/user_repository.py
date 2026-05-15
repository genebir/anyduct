"""User table access — read-only queries used by the auth flow.

A repository layer between routers and SQLAlchemy keeps router code free of
ORM details. Mutating user operations (create, set password, etc.) belong in
Step 8.5 alongside the workspaces/users CRUD endpoints.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.db.models import User


class UserRepository:
    """Async CRUD-read for ``users``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        # Email uniqueness is enforced at the DB level (workspace.py:43).
        result = await self._session.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()


__all__ = ["UserRepository"]
