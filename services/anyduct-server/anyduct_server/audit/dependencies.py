"""FastAPI ``Depends`` factories for the audit module.

Kept separate from :mod:`anyduct_server.audit.service` so the service itself
stays free of FastAPI-specific imports — handy for direct use in
background workers or scripts.
"""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.audit.service import AuditService, RequestMeta
from anyduct_server.dependencies import get_session


def get_audit_service(
    request: Request,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> AuditService:
    """Build a request-scoped :class:`AuditService`.

    Reads the :class:`RequestMeta` that
    :class:`AuditRequestMetaMiddleware` parked on ``request.state``; if
    the middleware didn't run (e.g. tests that bypass the stack), falls
    back to an empty meta so the service still works.
    """
    meta = getattr(request.state, "audit_meta", None)
    if not isinstance(meta, RequestMeta):
        meta = RequestMeta()
    return AuditService(session, request_meta=meta)


__all__ = ["get_audit_service"]
