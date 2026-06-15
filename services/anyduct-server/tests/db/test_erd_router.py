"""ERD diagram CRUD end-to-end (Phase AHD, ADR-0090).

Covers the create → list → get → update → delete round-trip, the
``table_count`` summary derived from ``design_json``, audit pairing, and
workspace isolation (a diagram in one workspace is 404 from another).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from anyduct_server.app_factory import create_app
from anyduct_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from anyduct_server.auth.password_service import PasswordService
from anyduct_server.db.enums import AuthMethod, WorkspaceRole
from anyduct_server.db.models import AuditLog, Membership, User, Workspace
from anyduct_server.dependencies import get_session
from anyduct_server.settings import Settings
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


def _build_app(session: AsyncSession) -> FastAPI:
    private, public = generate_rsa_keypair_pem(bits=2048)
    settings = Settings(
        database_url="postgresql+asyncpg://stub:stub@stub:5432/stub",  # pragma: allowlist secret
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


async def _seed_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        email=email.lower(),
        name="U",
        auth_method=AuthMethod.LOCAL,
        password_hash=PasswordService(rounds=4).hash("hunter2"),
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_workspace(
    session: AsyncSession, *, slug: str, user: User, role: WorkspaceRole
) -> Workspace:
    ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
    session.add(ws)
    await session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=user.id, role=role))
    await session.flush()
    return ws


async def _login(client: httpx.AsyncClient, *, email: str) -> str:
    resp = await client.post(
        "/auth/login",
        json={"email": email, "password": "hunter2"},  # pragma: allowlist secret
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def _sample_design() -> dict[str, Any]:
    return {
        "tables": [
            {
                "id": "t1",
                "name": "customers",
                "x": 0,
                "y": 0,
                "columns": [{"name": "id", "type": "BIGINT", "pk": True}],
            },
            {
                "id": "t2",
                "name": "orders",
                "x": 300,
                "y": 0,
                "columns": [
                    {"name": "id", "type": "BIGINT", "pk": True},
                    {"name": "customer_id", "type": "BIGINT", "pk": False},
                ],
            },
        ],
        "relations": [
            {
                "id": "r1",
                "from": "t2",
                "fromColumn": "customer_id",
                "to": "t1",
                "sourceCard": "many",
                "targetCard": "one",
            },
        ],
    }


async def test_erd_crud_round_trip(session: AsyncSession) -> None:
    user = await _seed_user(session, email="erd-crud@example.com")
    ws = await _seed_workspace(session, slug="erd-crud", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        h = {"Authorization": f"Bearer {token}"}

        # create
        resp = await client.post(
            f"/workspaces/{ws.id}/erd-diagrams",
            json={"name": "sales", "design_json": _sample_design()},
            headers=h,
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        did = created["id"]
        assert created["name"] == "sales"
        assert len(created["design_json"]["tables"]) == 2

        # list — summary carries table_count
        resp = await client.get(f"/workspaces/{ws.id}/erd-diagrams", headers=h)
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["id"] == did
        assert rows[0]["table_count"] == 2

        # get detail
        resp = await client.get(f"/workspaces/{ws.id}/erd-diagrams/{did}", headers=h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["design_json"]["relations"][0]["fromColumn"] == "customer_id"

        # patch (rename + add a table)
        design2 = _sample_design()
        design2["tables"].append({"id": "t3", "name": "products", "x": 0, "y": 300, "columns": []})
        resp = await client.patch(
            f"/workspaces/{ws.id}/erd-diagrams/{did}",
            json={"name": "sales_v2", "design_json": design2},
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "sales_v2"
        assert len(resp.json()["design_json"]["tables"]) == 3

        # delete → gone
        resp = await client.delete(f"/workspaces/{ws.id}/erd-diagrams/{did}", headers=h)
        assert resp.status_code == 204, resp.text
        resp = await client.get(f"/workspaces/{ws.id}/erd-diagrams/{did}", headers=h)
        assert resp.status_code == 404

    # audit trail: create + update + delete
    rows = (
        (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.resource_id == str(did))
                .order_by(AuditLog.created_at)
            )
        )
        .scalars()
        .all()
    )
    actions = [r.action for r in rows]
    assert actions == ["erd.create", "erd.update", "erd.delete"]


async def test_erd_workspace_isolation(session: AsyncSession) -> None:
    user = await _seed_user(session, email="erd-iso@example.com")
    ws_a = await _seed_workspace(session, slug="erd-iso-a", user=user, role=WorkspaceRole.EDITOR)
    ws_b = await _seed_workspace(session, slug="erd-iso-b", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        h = {"Authorization": f"Bearer {token}"}
        resp = await client.post(
            f"/workspaces/{ws_a.id}/erd-diagrams",
            json={"name": "only-in-a", "design_json": {"tables": [], "relations": []}},
            headers=h,
        )
        did = resp.json()["id"]
        # Same user, other workspace → the diagram is invisible (404).
        resp = await client.get(f"/workspaces/{ws_b.id}/erd-diagrams/{did}", headers=h)
        assert resp.status_code == 404
        resp = await client.get(f"/workspaces/{ws_b.id}/erd-diagrams", headers=h)
        assert resp.json() == []
