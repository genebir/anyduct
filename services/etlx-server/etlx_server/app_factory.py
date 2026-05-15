"""FastAPI application factory.

Step 8.1. ``create_app(settings)`` is the composition root: it wires routers,
middleware, and the lifespan-managed DB engine onto an immutable ``FastAPI``
instance. The module-level ``app`` in ``etlx_server.main`` is just
``create_app()`` with the default settings — useful for ``uvicorn
etlx_server.main:app``.

Tests typically build their own app via ``create_app(settings=...)`` to
override the database URL or environment without poking globals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from etlx_server import __version__ as server_version
from etlx_server.auth.jwt_service import JwtService
from etlx_server.auth.password_service import PasswordService
from etlx_server.db.session import make_engine, make_session_factory
from etlx_server.routers import auth as auth_router
from etlx_server.routers import health as health_router
from etlx_server.routers import meta as meta_router
from etlx_server.settings import Settings, get_settings

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def _build_jwt_service(settings: Settings) -> JwtService | None:
    """Construct a JwtService from settings, or ``None`` if no key is configured.

    A None return means the auth router will still load (so ``/auth/login``
    surfaces a useful 500 if invoked) — but the readiness probe and unrelated
    endpoints keep working. Tests always pass real keys via Settings.
    """
    private = settings.auth_jwt_private_key_pem
    public = settings.auth_jwt_public_key_pem
    if private is None and public is None:
        return None
    return JwtService(
        private_key_pem=private.get_secret_value() if private else None,
        public_key_pem=public.get_secret_value() if public else None,
        issuer=settings.auth_jwt_issuer,
        audience=settings.auth_jwt_audience,
        access_ttl_seconds=settings.auth_jwt_access_ttl_seconds,
        refresh_ttl_seconds=settings.auth_jwt_refresh_ttl_seconds,
    )


def _build_lifespan(settings: Settings) -> Lifespan:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Open one async engine per app instance; share via app.state so
        # endpoints/deps can pull it through `Depends(get_engine)` without
        # touching module globals.
        engine = make_engine(settings.database_url, echo=settings.database_echo)
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        # Auth services are constructed up front (no per-request cost) and
        # stay alive for the app's lifetime.
        app.state.password_service = PasswordService()
        jwt = _build_jwt_service(settings)
        if jwt is not None:
            app.state.jwt_service = jwt
        try:
            yield
        finally:
            await engine.dispose()

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construct a fully-wired FastAPI app.

    Pass ``settings`` explicitly in tests; production callers rely on
    :func:`etlx_server.settings.get_settings` (env-driven, cached).
    """
    resolved = settings if settings is not None else get_settings()
    app = FastAPI(
        title=resolved.service_name,
        description="ETL Plugins service backend.",
        version=server_version,
        lifespan=_build_lifespan(resolved),
        docs_url="/docs" if resolved.docs_enabled else None,
        redoc_url="/redoc" if resolved.docs_enabled else None,
    )

    # Make settings reachable even before the lifespan event fires (matters
    # for the OpenAPI schema generation that some test clients trigger).
    app.state.settings = resolved

    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=True,
        )

    app.include_router(health_router.router)
    app.include_router(meta_router.router)
    app.include_router(auth_router.router)
    return app


__all__ = ["create_app"]
