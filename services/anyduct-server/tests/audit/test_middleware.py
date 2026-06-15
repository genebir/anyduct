"""AuditRequestMetaMiddleware unit tests (Step 8.4).

Drives a minimal Starlette app via :class:`TestClient` so the middleware
is exercised exactly the way it would be in production — no
``call_next`` mocks, no synthetic Request objects.
"""

from __future__ import annotations

from anyduct_server.audit.middleware import AuditRequestMetaMiddleware
from anyduct_server.audit.service import RequestMeta
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def _build_app() -> Starlette:
    async def echo(request: Request) -> JSONResponse:
        meta = request.state.audit_meta
        return JSONResponse(
            {"ip": meta.ip, "user_agent": meta.user_agent, "is_meta": isinstance(meta, RequestMeta)}
        )

    app = Starlette(routes=[Route("/echo", endpoint=echo)])
    app.add_middleware(AuditRequestMetaMiddleware)
    return app


def test_middleware_attaches_request_meta_to_state() -> None:
    with TestClient(_build_app()) as client:
        resp = client.get("/echo", headers={"User-Agent": "pytest/1.0"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_meta"] is True
    assert body["user_agent"] == "pytest/1.0"
    # TestClient peer addr is "testclient" — we accept whatever Starlette reports.
    assert body["ip"] in {"testclient", None}


def test_middleware_handles_missing_user_agent() -> None:
    with TestClient(_build_app()) as client:
        # httpx (which TestClient wraps) sets a default UA; override to empty.
        resp = client.get("/echo", headers={"User-Agent": ""})
    assert resp.status_code == 200
    # Empty header still propagates as empty string; verify it's not None
    # only because the header was sent — we just confirm no crash.
    assert "user_agent" in resp.json()
