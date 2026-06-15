"""Workspace CRUD end-to-end integration tests (Step 8.5a).

Drives every endpoint added in 8.5a (POST / GET list / PATCH / DELETE) and
verifies the audit-row companion for each mutation. The existing
``GET /workspaces/{id}`` (Step 8.3) is covered in
``test_workspaces_router.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

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
    email: str,
    is_superadmin: bool = False,
) -> User:
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


async def _audit_rows_for(session: AsyncSession, workspace_id: str) -> list[AuditLog]:
    """Refresh and fetch — the router commits its own txn so the test session
    needs to actually re-query (not rely on identity map)."""
    await session.commit()  # release any savepoint so we see the router's writes
    result = await session.execute(
        select(AuditLog).where(AuditLog.resource_id == workspace_id).order_by(AuditLog.created_at)
    )
    return list(result.scalars().all())


# --- POST /workspaces -------------------------------------------------------


async def test_post_creates_workspace_and_owner_membership(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session, email="creator@example.com")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            "/workspaces",
            json={"name": "Demo", "slug": "demo-create", "color_hex": "#123456"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "demo-create"
    assert body["role"] == "owner"

    # Owner membership was inserted in the same txn.
    result = await session.execute(select(Membership).where(Membership.workspace_id == body["id"]))
    members = list(result.scalars().all())
    assert len(members) == 1
    assert members[0].role is WorkspaceRole.OWNER
    assert str(members[0].user_id) == str(user.id)

    # Audit row recorded under the new workspace.
    rows = await _audit_rows_for(session, body["id"])
    assert [r.action for r in rows] == ["workspace.create"]
    assert rows[0].after_json == {
        "name": "Demo",
        "slug": "demo-create",
        "color_hex": "#123456",
    }


async def test_post_returns_409_on_duplicate_slug(session: AsyncSession) -> None:
    user = await _seed_user(session, email="dup@example.com")
    session.add(Workspace(name="Existing", slug="taken-slug", color_hex="#000000"))
    await session.flush()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            "/workspaces",
            json={"name": "X", "slug": "taken-slug"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409
    assert "taken" in resp.json()["detail"].lower()


async def test_post_rejects_invalid_slug(session: AsyncSession) -> None:
    user = await _seed_user(session, email="badslug@example.com")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            "/workspaces",
            json={"name": "X", "slug": "UPPER"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422


# --- GET /workspaces (list) -------------------------------------------------


async def test_get_list_returns_only_user_memberships(
    session: AsyncSession,
) -> None:
    alice = await _seed_user(session, email="alice-list@example.com")
    bob = await _seed_user(session, email="bob-list@example.com")
    ws_a = Workspace(name="A", slug="list-a", color_hex="#000000")
    ws_b = Workspace(name="B", slug="list-b", color_hex="#111111")
    session.add_all([ws_a, ws_b])
    await session.flush()
    session.add_all(
        [
            Membership(workspace_id=ws_a.id, user_id=alice.id, role=WorkspaceRole.OWNER),
            Membership(workspace_id=ws_b.id, user_id=bob.id, role=WorkspaceRole.OWNER),
        ]
    )
    await session.flush()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=alice.email)
        resp = await client.get("/workspaces", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    slugs = {w["slug"] for w in resp.json()}
    assert slugs == {"list-a"}  # Alice doesn't see Bob's


async def test_get_list_superadmin_sees_all(session: AsyncSession) -> None:
    admin = await _seed_user(session, email="admin-list@example.com", is_superadmin=True)
    session.add_all(
        [
            Workspace(name="X", slug="list-admin-x", color_hex="#000000"),
            Workspace(name="Y", slug="list-admin-y", color_hex="#000000"),
        ]
    )
    await session.flush()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=admin.email)
        resp = await client.get("/workspaces", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    slugs = {w["slug"] for w in resp.json()}
    assert {"list-admin-x", "list-admin-y"}.issubset(slugs)


# --- PATCH /workspaces/{id} -------------------------------------------------


async def test_patch_editor_can_update(session: AsyncSession) -> None:
    user = await _seed_user(session, email="editor-patch@example.com")
    ws = Workspace(name="Old", slug="patch-target", color_hex="#000000")
    session.add(ws)
    await session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=user.id, role=WorkspaceRole.EDITOR))
    await session.flush()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.patch(
            f"/workspaces/{ws.id}",
            json={"name": "New Name"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "New Name"

    rows = await _audit_rows_for(session, str(ws.id))
    assert [r.action for r in rows] == ["workspace.update"]
    assert rows[0].before_json == {"name": "Old"}
    assert rows[0].after_json == {"name": "New Name"}


async def test_patch_runner_forbidden(session: AsyncSession) -> None:
    user = await _seed_user(session, email="runner-patch@example.com")
    ws = Workspace(name="X", slug="patch-runner", color_hex="#000000")
    session.add(ws)
    await session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=user.id, role=WorkspaceRole.RUNNER))
    await session.flush()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.patch(
            f"/workspaces/{ws.id}",
            json={"name": "Try"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


async def test_patch_empty_body_returns_400(session: AsyncSession) -> None:
    user = await _seed_user(session, email="empty-patch@example.com")
    ws = Workspace(name="X", slug="patch-empty", color_hex="#000000")
    session.add(ws)
    await session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=user.id, role=WorkspaceRole.OWNER))
    await session.flush()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.patch(
            f"/workspaces/{ws.id}",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 400


async def test_patch_slug_collision_returns_409(session: AsyncSession) -> None:
    user = await _seed_user(session, email="slug-collide@example.com")
    ws_a = Workspace(name="A", slug="patch-collide-a", color_hex="#000000")
    ws_b = Workspace(name="B", slug="patch-collide-b", color_hex="#000000")
    session.add_all([ws_a, ws_b])
    await session.flush()
    session.add(Membership(workspace_id=ws_a.id, user_id=user.id, role=WorkspaceRole.OWNER))
    await session.flush()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.patch(
            f"/workspaces/{ws_a.id}",
            json={"slug": "patch-collide-b"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409


# --- DELETE /workspaces/{id} ------------------------------------------------


async def test_delete_owner_can_remove(session: AsyncSession) -> None:
    user = await _seed_user(session, email="owner-del@example.com")
    ws = Workspace(name="X", slug="delete-target", color_hex="#000000")
    session.add(ws)
    await session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=user.id, role=WorkspaceRole.OWNER))
    await session.flush()
    ws_id = str(ws.id)

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.delete(
            f"/workspaces/{ws.id}", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 204

    # Workspace gone.
    gone = (
        await session.execute(select(Workspace).where(Workspace.id == ws.id))
    ).scalar_one_or_none()
    assert gone is None
    # Audit row stays (workspace_id FK is SET NULL).
    rows = await _audit_rows_for(session, ws_id)
    assert [r.action for r in rows] == ["workspace.delete"]
    assert rows[0].workspace_id is None  # SET NULL after the cascade
    assert rows[0].before_json == {"name": "X", "slug": "delete-target", "color_hex": "#000000"}


async def test_delete_editor_forbidden(session: AsyncSession) -> None:
    user = await _seed_user(session, email="editor-del@example.com")
    ws = Workspace(name="X", slug="delete-editor", color_hex="#000000")
    session.add(ws)
    await session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=user.id, role=WorkspaceRole.EDITOR))
    await session.flush()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.delete(
            f"/workspaces/{ws.id}", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 403
