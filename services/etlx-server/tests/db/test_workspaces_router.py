"""GET /workspaces/{workspace_id} integration tests (Step 8.3).

Drives the full RBAC dependency chain against a real metadata DB:

* ``get_current_user`` (Bearer JWT)
* ``get_current_workspace`` (workspace lookup + membership check)
* ``require_workspace_role(VIEWER)`` (role precedence + superadmin bypass)

Each test seeds a user and (optionally) a membership, logs in via the local
auth route to get a real access token, then hits the workspace endpoint.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from etlx_server.app_factory import create_app
from etlx_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from etlx_server.auth.password_service import PasswordService
from etlx_server.db.enums import AuthMethod, WorkspaceRole
from etlx_server.db.models import Membership, User, Workspace
from etlx_server.dependencies import get_session
from etlx_server.settings import Settings
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


# --- fixtures ---------------------------------------------------------------


def _build_app(session: AsyncSession) -> FastAPI:
    private, public = generate_rsa_keypair_pem(bits=2048)
    settings = Settings(
        database_url="postgresql+asyncpg://stub:stub@stub:5432/stub",
        auth_jwt_private_key_pem=private.decode("utf-8"),
        auth_jwt_public_key_pem=public.decode("utf-8"),
        auth_jwt_access_ttl_seconds=60,
        auth_jwt_refresh_ttl_seconds=120,
    )
    app = create_app(settings=settings)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override_session
    app.state.password_service = PasswordService(rounds=4)
    app.state.jwt_service = JwtService(
        private_key_pem=private,
        public_key_pem=public,
        issuer=settings.auth_jwt_issuer,
        audience=settings.auth_jwt_audience,
        access_ttl_seconds=settings.auth_jwt_access_ttl_seconds,
        refresh_ttl_seconds=settings.auth_jwt_refresh_ttl_seconds,
    )
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_user(
    session: AsyncSession,
    *,
    email: str = "u@example.com",
    password: str = "hunter2",
    is_superadmin: bool = False,
) -> User:
    user = User(
        email=email.lower(),
        name="U",
        auth_method=AuthMethod.LOCAL,
        password_hash=PasswordService(rounds=4).hash(password),
        is_superadmin=is_superadmin,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_workspace(session: AsyncSession, *, slug: str = "demo") -> Workspace:
    ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
    session.add(ws)
    await session.flush()
    return ws


async def _seed_membership(
    session: AsyncSession, *, ws: Workspace, user: User, role: WorkspaceRole
) -> None:
    session.add(Membership(workspace_id=ws.id, user_id=user.id, role=role))
    await session.flush()


async def _login(client: httpx.AsyncClient, *, email: str, password: str = "hunter2") -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


# --- tests ------------------------------------------------------------------


async def test_unauthenticated_returns_401(session: AsyncSession) -> None:
    ws = await _seed_workspace(session)
    app = _build_app(session)
    async with _client(app) as client:
        resp = await client.get(f"/workspaces/{ws.id}")
    assert resp.status_code == 401


async def test_unknown_workspace_returns_404(session: AsyncSession) -> None:
    user = await _seed_user(session)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


async def test_non_member_returns_403(session: AsyncSession) -> None:
    user = await _seed_user(session)
    ws = await _seed_workspace(session)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 403
    assert "not a member" in resp.json()["detail"]


async def test_viewer_member_can_read(session: AsyncSession) -> None:
    user = await _seed_user(session, email="viewer@example.com")
    ws = await _seed_workspace(session, slug="viewer-ws")
    await _seed_membership(session, ws=ws, user=user, role=WorkspaceRole.VIEWER)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(ws.id)
    assert body["slug"] == "viewer-ws"
    assert body["role"] == "viewer"


async def test_owner_member_can_read(session: AsyncSession) -> None:
    user = await _seed_user(session, email="owner@example.com")
    ws = await _seed_workspace(session, slug="owner-ws")
    await _seed_membership(session, ws=ws, user=user, role=WorkspaceRole.OWNER)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200
    assert resp.json()["role"] == "owner"


async def test_superadmin_can_read_without_membership(session: AsyncSession) -> None:
    user = await _seed_user(session, email="admin@example.com", is_superadmin=True)
    ws = await _seed_workspace(session, slug="admin-ws")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200
    body = resp.json()
    # No membership row -> role is null in the response.
    assert body["role"] is None


async def test_malformed_workspace_id_returns_422(session: AsyncSession) -> None:
    user = await _seed_user(session)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            "/workspaces/not-a-uuid",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422
