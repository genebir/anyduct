"""AuditLogRepository — read-side queries for the ``/audit`` endpoint."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.db.models import AuditLog


class AuditLogRepository:
    """Async filtered read for ``audit_log``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def query(
        self,
        *,
        workspace_id: UUID | None = None,
        actor_user_id: UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditLog]:
        """Return matching rows sorted newest-first.

        ``limit`` is bounded at the router (Pydantic validation); here we
        trust the caller. ``offset`` pagination is fine for the volumes
        Step 8 targets — cursor pagination can land in Step 11 if needed.
        """
        stmt = select(AuditLog)
        if workspace_id is not None:
            stmt = stmt.where(AuditLog.workspace_id == workspace_id)
        if actor_user_id is not None:
            stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
        if resource_type is not None:
            stmt = stmt.where(AuditLog.resource_type == resource_type)
        if resource_id is not None:
            stmt = stmt.where(AuditLog.resource_id == resource_id)
        # Secondary sort on ``id`` (UUIDv7 — temporally ordered, see
        # ADR-0020) breaks ties when two audit rows happen to land in
        # the same microsecond. Without it the ``/audit`` response
        # order is non-deterministic on bursty test traffic.
        stmt = (
            stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


__all__ = ["AuditLogRepository"]
