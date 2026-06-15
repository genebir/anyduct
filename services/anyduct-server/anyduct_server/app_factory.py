"""FastAPI application factory.

Step 8.1. ``create_app(settings)`` is the composition root: it wires routers,
middleware, and the lifespan-managed DB engine onto an immutable ``FastAPI``
instance. The module-level ``app`` in ``anyduct_server.main`` is just
``create_app()`` with the default settings — useful for ``uvicorn
anyduct_server.main:app``.

Tests typically build their own app via ``create_app(settings=...)`` to
override the database URL or environment without poking globals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from anyduct_server import __version__ as server_version
from anyduct_server.audit.middleware import AuditRequestMetaMiddleware
from anyduct_server.auth.jwt_service import JwtService
from anyduct_server.auth.oidc_service import OidcService
from anyduct_server.auth.oidc_state import OidcStateSigner
from anyduct_server.auth.password_service import PasswordService
from anyduct_server.db.session import make_engine, make_session_factory
from anyduct_server.routers import assets as assets_router
from anyduct_server.routers import audit as audit_router
from anyduct_server.routers import auth as auth_router
from anyduct_server.routers import connections as connections_router
from anyduct_server.routers import erd as erd_router
from anyduct_server.routers import health as health_router
from anyduct_server.routers import memberships as memberships_router
from anyduct_server.routers import meta as meta_router
from anyduct_server.routers import oidc as oidc_router
from anyduct_server.routers import pipelines as pipelines_router
from anyduct_server.routers import runs as runs_router
from anyduct_server.routers import schedules as schedules_router
from anyduct_server.routers import sensors as sensors_router
from anyduct_server.routers import variables as variables_router
from anyduct_server.routers import workspaces as workspaces_router
from anyduct_server.settings import Settings, get_settings
from etl_plugins.config.secrets import SecretBackend, get_secret_backend

logger = structlog.get_logger(__name__)

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


def _build_oidc_service(settings: Settings) -> OidcService | None:
    """Construct an :class:`OidcService` when OIDC is configured.

    Returns ``None`` when no providers were declared *or* when the JWT keypair
    is missing — without those keys, state tokens can't be signed and the
    callback flow would never succeed.
    """
    if not settings.auth_oidc_providers:
        return None
    private = settings.auth_jwt_private_key_pem
    public = settings.auth_jwt_public_key_pem
    if private is None or public is None:
        return None
    state_signer = OidcStateSigner(
        private_key_pem=private.get_secret_value().encode("utf-8"),
        public_key_pem=public.get_secret_value().encode("utf-8"),
        issuer=settings.auth_jwt_issuer,
        ttl_seconds=settings.auth_oidc_state_ttl_seconds,
    )
    return OidcService(
        providers=settings.auth_oidc_providers,
        state_signer=state_signer,
        http_timeout_seconds=settings.auth_oidc_http_timeout_seconds,
    )


def _build_secret_backend(settings: Settings) -> SecretBackend:
    """Construct the configured :class:`SecretBackend`. Honors Settings.secret_backend."""
    opts: dict[str, str] = {}
    if settings.secret_backend_file_path:
        opts["file_path"] = settings.secret_backend_file_path
    return get_secret_backend(settings.secret_backend, **opts)


def _build_lifespan(settings: Settings) -> Lifespan:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Phase AAQ post-mortem (2026-05-29) — pre-load every built-in
        # connector at server startup so the registry is fully populated
        # even when the installed ``entry_points`` metadata is stale
        # (the user-visible symptom was Vertica + MSSQL silently missing
        # from ``Available`` despite a successful ``pyproject.toml``
        # edit). The fallback inside ``ConnectorRegistry.list_connectors``
        # imports each builtin module by path, so a missing extra is a
        # warning, not a crash.
        from etl_plugins.core.registry import ConnectorRegistry

        loaded = ConnectorRegistry.list_connectors()
        logger.info(
            "connector registry loaded",
            extra={"connectors": loaded, "count": len(loaded)},
        )

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
        oidc = _build_oidc_service(settings)
        if oidc is not None:
            app.state.oidc_service = oidc
        app.state.secret_backend = _build_secret_backend(settings)
        try:
            yield
        finally:
            await engine.dispose()

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construct a fully-wired FastAPI app.

    Pass ``settings`` explicitly in tests; production callers rely on
    :func:`anyduct_server.settings.get_settings` (env-driven, cached).
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

    # Audit metadata capture sits in front of every handler so any Depends-
    # built AuditService picks up the request's IP + User-Agent.
    app.add_middleware(AuditRequestMetaMiddleware)

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
    app.include_router(oidc_router.router)
    app.include_router(workspaces_router.router)
    app.include_router(memberships_router.router)
    app.include_router(connections_router.router)
    app.include_router(pipelines_router.router)
    app.include_router(schedules_router.router)
    app.include_router(runs_router.router)
    app.include_router(audit_router.router)
    app.include_router(assets_router.router)
    app.include_router(variables_router.router)
    app.include_router(sensors_router.router)
    app.include_router(erd_router.router)
    return app


__all__ = ["create_app"]
