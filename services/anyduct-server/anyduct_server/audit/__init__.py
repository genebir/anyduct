"""Audit logging (Step 8.4, ADR-0023 §9).

Every mutating endpoint records a row in ``audit_log`` *within its own
transaction* — so if the business mutation rolls back, the audit row rolls
back with it (no false positives in the audit trail).

Public surface:

* :class:`AuditService` — session-bound recorder. Endpoint code calls
  ``audit.record(...)`` after a successful mutation.
* :class:`AuditLogRepository` — read-side query for the ``/audit`` API.
* :class:`AuditRequestMetaMiddleware` — captures ``ip`` + ``user_agent``
  off the incoming request into ``request.state`` so handlers don't have
  to plumb them manually.
* :class:`RequestMeta` — small dataclass moved between middleware,
  Depends, and service.

Mutating endpoint pattern::

    audit: AuditService = Depends(get_audit_service)
    ...
    workspace = await repo.create(...)
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=workspace.id,
        action="workspace.create",
        resource_type="workspace",
        resource_id=str(workspace.id),
        before=None,
        after={"name": workspace.name, "slug": workspace.slug},
    )
    # commit at request boundary
"""

from anyduct_server.audit.middleware import AuditRequestMetaMiddleware
from anyduct_server.audit.repository import AuditLogRepository
from anyduct_server.audit.service import AuditService, RequestMeta

__all__ = [
    "AuditLogRepository",
    "AuditRequestMetaMiddleware",
    "AuditService",
    "RequestMeta",
]
