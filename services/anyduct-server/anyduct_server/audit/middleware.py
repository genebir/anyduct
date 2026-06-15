"""ASGI middleware that captures client IP + User-Agent for the audit log.

Sits in front of the routers so every Depends-built :class:`AuditService`
sees the values without each endpoint having to thread ``request`` through
manually.

We attach a single :class:`RequestMeta` to ``request.state.audit_meta``.
The values are advisory — the IP is whatever the ASGI server reports, and
the UA is whatever the client chose to send. Behind a trusted reverse
proxy operators should additionally consume ``X-Forwarded-For`` /
``X-Real-IP``; that wiring lives in Step 11 (Operability) since it depends
on the deployment topology.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from anyduct_server.audit.service import RequestMeta


class AuditRequestMetaMiddleware(BaseHTTPMiddleware):
    """Populate ``request.state.audit_meta`` with the caller's IP + UA."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.audit_meta = RequestMeta(
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return await call_next(request)


__all__ = ["AuditRequestMetaMiddleware"]
