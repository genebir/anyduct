"""GET /audit end-to-end integration tests (Step 8.4).

Drives the full ACL chain: middleware-populated request meta, JWT-based
``get_current_user``, then either the SuperAdmin global path or the
``MembershipRepository`` workspace check.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from etlx_server.app_factory import create_app
from etlx_server.audit.service import AuditService
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


async def _seed_user(session: AsyncSession, *, email: str, is_superadmin: bool = False) -> User:
    user = User(
        email=email.lower(),
        name="U",
        auth_method=AuthMethod.LOCAL,
        password_hash=PasswordService(rounds=4).hash("hunter2"),
        is_superadmin=is_superadmin,
    )
    session.add(user)
    await session.flush()
    return user


async def _login(client: httpx.AsyncClient, *, email: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": "hunter2"})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


async def test_unauthenticated_returns_401(session: AsyncSession) -> None:
    app = _build_app(session)
    async with _client(app) as client:
        resp = await client.get("/audit")
    assert resp.status_code == 401


async def test_non_admin_global_query_returns_403(session: AsyncSession) -> None:
    user = await _seed_user(session, email="nobody@example.com")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get("/audit", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert "SuperAdmin" in resp.json()["detail"]


async def test_superadmin_can_query_globally(session: AsyncSession) -> None:
    admin = await _seed_user(session, email="admin@example.com", is_superadmin=True)
    ws_a = Workspace(name="A", slug="audit-ws-a", color_hex="#000000")
    ws_b = Workspace(name="B", slug="audit-ws-b", color_hex="#111111")
    session.add_all([ws_a, ws_b])
    await session.flush()

    audit = AuditService(session)
    await audit.record(
        actor_user_id=admin.id, workspace_id=ws_a.id, action="a.x", resource_type="r"
    )
    await audit.record(
        actor_user_id=admin.id, workspace_id=ws_b.id, action="b.y", resource_type="r"
    )

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=admin.email)
        resp = await client.get("/audit", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    actions = {row["action"] for row in resp.json()}
    assert actions == {"a.x", "b.y"}


async def test_workspace_query_requires_membership(session: AsyncSession) -> None:
    """Non-member, non-SuperAdmin asking for a workspace's audit log → 403."""
    user = await _seed_user(session, email="stranger@example.com")
    ws = Workspace(name="X", slug="audit-stranger-ws", color_hex="#000000")
    session.add(ws)
    await session.flush()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/audit?workspace_id={ws.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403
    assert "not a member" in resp.json()["detail"]


async def test_viewer_member_can_query_their_workspace(session: AsyncSession) -> None:
    user = await _seed_user(session, email="viewer@example.com")
    ws = Workspace(name="X", slug="audit-viewer-ws", color_hex="#000000")
    session.add(ws)
    await session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=user.id, role=WorkspaceRole.VIEWER))
    await session.flush()

    await AuditService(session).record(
        actor_user_id=user.id,
        workspace_id=ws.id,
        action="x.create",
        resource_type="x",
        resource_id="abc",
    )

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/audit?workspace_id={ws.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["action"] == "x.create"
    assert body[0]["workspace_id"] == str(ws.id)


async def test_filters_combine(session: AsyncSession) -> None:
    admin = await _seed_user(session, email="admin2@example.com", is_superadmin=True)
    ws = Workspace(name="X", slug="audit-filter-ws", color_hex="#000000")
    session.add(ws)
    await session.flush()
    audit = AuditService(session)
    await audit.record(
        actor_user_id=admin.id,
        workspace_id=ws.id,
        action="a",
        resource_type="conn",
        resource_id="c1",
    )
    await audit.record(
        actor_user_id=admin.id,
        workspace_id=ws.id,
        action="b",
        resource_type="pipe",
        resource_id="p1",
    )

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=admin.email)
        resp = await client.get(
            f"/audit?workspace_id={ws.id}&resource_type=conn",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    actions = [row["action"] for row in resp.json()]
    assert actions == ["a"]


async def test_limit_bounds_enforced(session: AsyncSession) -> None:
    admin = await _seed_user(session, email="admin3@example.com", is_superadmin=True)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=admin.email)
        resp = await client.get("/audit?limit=0", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422


async def test_unknown_workspace_filter_returns_403_to_non_admin(
    session: AsyncSession,
) -> None:
    """Membership check happens before the row query — non-member of a
    non-existent workspace is indistinguishable from non-member of an
    existing one (both 403). Stops UUID enumeration."""
    user = await _seed_user(session, email="probe@example.com")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/audit?workspace_id={uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403
