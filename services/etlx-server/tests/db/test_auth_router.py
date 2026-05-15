"""End-to-end auth flow against a real metadata DB (Step 8.2a).

Seeds a local user with a bcrypt-hashed password, drives the FastAPI app via
``httpx.AsyncClient(ASGITransport(app))``, and walks:

  /auth/login → /auth/me → /auth/refresh → /auth/logout

plus the surrounding failure modes (bad credentials, wrong token type,
disabled local auth).

The conftest ``session`` fixture wraps every test in an outer transaction
that rolls back on teardown — meaning per-session ``commit()`` calls only
release a savepoint, so a *separate* engine connection (the one the FastAPI
lifespan would open) would not see the seed. We side-step that by overriding
the ``get_session`` dependency so the route handlers reuse the same test
session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from etlx_server.app_factory import create_app
from etlx_server.auth.jwt_service import (
    JwtService,
    generate_rsa_keypair_pem,
)
from etlx_server.auth.password_service import PasswordService
from etlx_server.db.enums import AuthMethod
from etlx_server.db.models import User
from etlx_server.dependencies import get_session
from etlx_server.settings import Settings
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


# --- helpers ----------------------------------------------------------------


def _make_settings(*, local_enabled: bool = True) -> Settings:
    """Settings with a fresh keypair. ``database_url`` is unused — the test's
    session is wired in via dependency_overrides — but Settings requires the
    field, so we set a stub."""
    private_pem, public_pem = generate_rsa_keypair_pem(bits=2048)
    return Settings(
        database_url="postgresql+asyncpg://stub:stub@stub:5432/stub",
        auth_jwt_private_key_pem=private_pem.decode("utf-8"),
        auth_jwt_public_key_pem=public_pem.decode("utf-8"),
        auth_jwt_access_ttl_seconds=60,
        auth_jwt_refresh_ttl_seconds=120,
        auth_local_enabled=local_enabled,
    )


def _build_app(session: AsyncSession, *, local_enabled: bool = True) -> FastAPI:
    """Construct an app whose ``get_session`` Depends returns the test session.

    Also pre-attaches PasswordService + JwtService onto app.state so endpoint
    handlers don't depend on the lifespan firing (we don't open a real engine
    here; routes touch the DB through the overridden session).
    """
    settings = _make_settings(local_enabled=local_enabled)
    app = create_app(settings=settings)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        # Yield the existing test session; commits become savepoint releases
        # (per conftest), so the outer rollback still cleans everything up.
        yield session

    app.dependency_overrides[get_session] = _override_session

    # Skip the lifespan: attach services directly. The lifespan would also
    # open a real engine which we don't need.
    app.state.password_service = PasswordService(rounds=4)
    app.state.jwt_service = JwtService(
        private_key_pem=settings.auth_jwt_private_key_pem.get_secret_value(),  # type: ignore[union-attr]
        public_key_pem=settings.auth_jwt_public_key_pem.get_secret_value()  # type: ignore[union-attr]
        if settings.auth_jwt_public_key_pem
        else None,
        issuer=settings.auth_jwt_issuer,
        audience=settings.auth_jwt_audience,
        access_ttl_seconds=settings.auth_jwt_access_ttl_seconds,
        refresh_ttl_seconds=settings.auth_jwt_refresh_ttl_seconds,
    )
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_local_user(
    session: AsyncSession,
    *,
    email: str = "alice@example.com",
    password: str = "hunter2",
    auth_method: AuthMethod = AuthMethod.LOCAL,
) -> User:
    pw_service = PasswordService(rounds=4)
    user = User(
        email=email.lower(),
        name="Alice",
        auth_method=auth_method,
        password_hash=pw_service.hash(password) if auth_method is AuthMethod.LOCAL else None,
    )
    session.add(user)
    await session.flush()
    return user


# --- tests ------------------------------------------------------------------


async def test_login_returns_token_pair(session: AsyncSession) -> None:
    await _seed_local_user(session)
    app = _build_app(session)
    async with _client(app) as client:
        resp = await client.post(
            "/auth/login", json={"email": "alice@example.com", "password": "hunter2"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["expires_in"] == 60


async def test_login_wrong_password_returns_401(session: AsyncSession) -> None:
    await _seed_local_user(session)
    app = _build_app(session)
    async with _client(app) as client:
        resp = await client.post(
            "/auth/login", json={"email": "alice@example.com", "password": "bogus"}
        )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid email or password"


async def test_login_unknown_email_returns_401(session: AsyncSession) -> None:
    app = _build_app(session)
    async with _client(app) as client:
        resp = await client.post(
            "/auth/login", json={"email": "ghost@example.com", "password": "x"}
        )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid email or password"


async def test_oidc_user_cannot_use_local_login(session: AsyncSession) -> None:
    await _seed_local_user(
        session, email="bob@example.com", auth_method=AuthMethod.OIDC_GOOGLE
    )
    app = _build_app(session)
    async with _client(app) as client:
        resp = await client.post(
            "/auth/login", json={"email": "bob@example.com", "password": "anything"}
        )
    assert resp.status_code == 401


async def test_login_disabled_returns_503(session: AsyncSession) -> None:
    await _seed_local_user(session)
    app = _build_app(session, local_enabled=False)
    async with _client(app) as client:
        resp = await client.post(
            "/auth/login", json={"email": "alice@example.com", "password": "hunter2"}
        )
    assert resp.status_code == 503


async def test_me_returns_authenticated_user(session: AsyncSession) -> None:
    user = await _seed_local_user(session)
    app = _build_app(session)
    async with _client(app) as client:
        login = await client.post(
            "/auth/login", json={"email": "alice@example.com", "password": "hunter2"}
        )
        access = login.json()["access_token"]
        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "alice@example.com"
    assert body["id"] == str(user.id)


async def test_me_without_token_returns_401(session: AsyncSession) -> None:
    app = _build_app(session)
    async with _client(app) as client:
        resp = await client.get("/auth/me")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


async def test_me_with_garbage_token_returns_401(session: AsyncSession) -> None:
    app = _build_app(session)
    async with _client(app) as client:
        resp = await client.get("/auth/me", headers={"Authorization": "Bearer not.a.real.jwt"})
    assert resp.status_code == 401


async def test_me_rejects_refresh_token_in_authorization_header(
    session: AsyncSession,
) -> None:
    await _seed_local_user(session)
    app = _build_app(session)
    async with _client(app) as client:
        login = await client.post(
            "/auth/login", json={"email": "alice@example.com", "password": "hunter2"}
        )
        refresh_token = login.json()["refresh_token"]
        resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {refresh_token}"})
    assert resp.status_code == 401
    assert "wrong token type" in resp.json()["detail"]


async def test_refresh_returns_new_pair(session: AsyncSession) -> None:
    await _seed_local_user(session)
    app = _build_app(session)
    async with _client(app) as client:
        login = await client.post(
            "/auth/login", json={"email": "alice@example.com", "password": "hunter2"}
        )
        refresh_token = login.json()["refresh_token"]
        resp = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    new_body = resp.json()
    assert new_body["access_token"]
    assert new_body["refresh_token"]


async def test_refresh_with_access_token_rejected(session: AsyncSession) -> None:
    await _seed_local_user(session)
    app = _build_app(session)
    async with _client(app) as client:
        login = await client.post(
            "/auth/login", json={"email": "alice@example.com", "password": "hunter2"}
        )
        access = login.json()["access_token"]
        resp = await client.post("/auth/refresh", json={"refresh_token": access})
    assert resp.status_code == 401


async def test_logout_returns_204(session: AsyncSession) -> None:
    await _seed_local_user(session)
    app = _build_app(session)
    async with _client(app) as client:
        login = await client.post(
            "/auth/login", json={"email": "alice@example.com", "password": "hunter2"}
        )
        access = login.json()["access_token"]
        resp = await client.post("/auth/logout", headers={"Authorization": f"Bearer {access}"})
    assert resp.status_code == 204
    assert resp.content == b""


async def test_logout_without_token_returns_401(session: AsyncSession) -> None:
    app = _build_app(session)
    async with _client(app) as client:
        resp = await client.post("/auth/logout")
    assert resp.status_code == 401
