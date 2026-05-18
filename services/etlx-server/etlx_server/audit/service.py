"""AuditService — record a row per mutation, in the caller's transaction.

The service intentionally does **not** open/commit/rollback its own session;
it works off the request-scoped :class:`AsyncSession` so the audit row
participates in the same transaction as the business mutation. If the
endpoint rolls back, so does the audit row — which is the only correct
semantics for an audit trail (a row claiming "user X did Y" must never
appear when Y didn't actually happen).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.db.models import AuditLog


@dataclass(frozen=True)
class RequestMeta:
    """Per-request audit metadata captured by the middleware.

    Empty values are normal — health probes, OPTIONS preflights, and ASGI
    transports without a client peer (TestClient) all produce ``None``.
    """

    ip: str | None = None
    user_agent: str | None = None


class AuditService:
    """Append-only audit-log recorder bound to a single session.

    A new instance is constructed per request via
    :func:`etlx_server.audit.dependencies.get_audit_service`. Callers use
    ``await audit.record(...)`` *after* a successful mutation and before
    the request boundary commits the session.
    """

    def __init__(self, session: AsyncSession, *, request_meta: RequestMeta | None = None) -> None:
        self._session = session
        self._meta = request_meta or RequestMeta()

    async def record(
        self,
        *,
        action: str,
        resource_type: str,
        actor_user_id: UUID | None,
        workspace_id: UUID | None = None,
        resource_id: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Add a single :class:`AuditLog` row to the session.

        The row is flushed (so its ``id`` and ``created_at`` are populated
        before the caller continues) but **not committed** — the caller
        owns the transaction boundary. Any rollback in the wrapping
        request removes the audit row along with the failed mutation.
        """
        row = AuditLog(
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before_json=before,
            after_json=after,
            ip=self._meta.ip,
            user_agent=self._meta.user_agent,
        )
        self._session.add(row)
        await self._session.flush()
        return row


__all__ = ["AuditService", "RequestMeta"]
