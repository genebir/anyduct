"""Membership router end-to-end integration tests (Step 8.5b).

Covers every endpoint plus the last-Owner safeguard (the most-likely
foot-gun in this slice).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

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


async def _seed_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        email=email.lower(),
        name=email.split("@")[0].title(),
        auth_method=AuthMethod.LOCAL,
        password_hash=PasswordService(rounds=4).hash("hunter2"),
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_workspace_with_owner(session: AsyncSession, *, slug: str, owner: User) -> Workspace:
    ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
    session.add(ws)
    await session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=owner.id, role=WorkspaceRole.OWNER))
    await session.flush()
    return ws


async def _login(client: httpx.AsyncClient, *, email: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": "hunter2"})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


async def _audit_rows(session: AsyncSession, resource_id: UUID) -> list[AuditLog]:
    await session.commit()
    result = await session.execute(
        select(AuditLog)
        .where(AuditLog.resource_id == str(resource_id))
        .order_by(AuditLog.created_at)
    )
    return list(result.scalars().all())


# --- GET (list) -------------------------------------------------------------


async def test_list_returns_members(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="owner-l@example.com")
    viewer = await _seed_user(session, email="viewer-l@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-list", owner=owner)
    session.add(Membership(workspace_id=ws.id, user_id=viewer.id, role=WorkspaceRole.VIEWER))
    await session.flush()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=viewer.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/memberships",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    emails = {row["email"]: row["role"] for row in body}
    assert emails == {
        "owner-l@example.com": "owner",
        "viewer-l@example.com": "viewer",
    }


async def test_list_non_member_returns_403(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="owner-403@example.com")
    stranger = await _seed_user(session, email="stranger-403@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-403", owner=owner)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=stranger.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/memberships",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


# --- POST -------------------------------------------------------------------


async def test_post_owner_adds_editor(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="owner-add@example.com")
    invitee = await _seed_user(session, email="newbie@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-add", owner=owner)

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/memberships",
            json={"email": invitee.email, "role": "editor"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == invitee.email
    assert body["role"] == "editor"

    rows = await _audit_rows(session, UUID(body["id"]))
    assert [r.action for r in rows] == ["membership.create"]
    assert rows[0].after_json == {"user_id": str(invitee.id), "role": "editor"}


async def test_post_non_owner_forbidden(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="owner-non@example.com")
    editor = await _seed_user(session, email="editor-non@example.com")
    target = await _seed_user(session, email="target-non@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-non", owner=owner)
    session.add(Membership(workspace_id=ws.id, user_id=editor.id, role=WorkspaceRole.EDITOR))
    await session.flush()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=editor.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/memberships",
            json={"email": target.email, "role": "viewer"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


async def test_post_unknown_email_returns_404(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="owner-unknown@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-unknown", owner=owner)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/memberships",
            json={"email": "nobody@example.com", "role": "viewer"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


async def test_post_duplicate_returns_409(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="owner-dup@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-dup", owner=owner)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/memberships",
            json={"email": owner.email, "role": "editor"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409


async def test_post_invalid_role_returns_422(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="owner-bad@example.com")
    other = await _seed_user(session, email="other-bad@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-bad", owner=owner)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/memberships",
            json={"email": other.email, "role": "god"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422


# --- PATCH ------------------------------------------------------------------


async def test_patch_changes_role_and_records_audit(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="owner-patch@example.com")
    other = await _seed_user(session, email="other-patch@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-patch", owner=owner)
    member = Membership(workspace_id=ws.id, user_id=other.id, role=WorkspaceRole.VIEWER)
    session.add(member)
    await session.flush()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.patch(
            f"/workspaces/{ws.id}/memberships/{other.id}",
            json={"role": "editor"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["role"] == "editor"

    rows = await _audit_rows(session, member.id)
    assert [r.action for r in rows] == ["membership.update"]
    assert rows[0].before_json == {"role": "viewer"}
    assert rows[0].after_json == {"role": "editor"}


async def test_patch_last_owner_demotion_returns_409(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="lonely-owner@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-lonely", owner=owner)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.patch(
            f"/workspaces/{ws.id}/memberships/{owner.id}",
            json={"role": "editor"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409
    assert "last Owner" in resp.json()["detail"]


async def test_patch_owner_demote_allowed_when_other_owner_exists(
    session: AsyncSession,
) -> None:
    owner_a = await _seed_user(session, email="owner-a@example.com")
    owner_b = await _seed_user(session, email="owner-b@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-two-owners", owner=owner_a)
    session.add(Membership(workspace_id=ws.id, user_id=owner_b.id, role=WorkspaceRole.OWNER))
    await session.flush()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner_a.email)
        resp = await client.patch(
            f"/workspaces/{ws.id}/memberships/{owner_a.id}",
            json={"role": "viewer"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["role"] == "viewer"


async def test_patch_unknown_membership_returns_404(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="owner-404@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-404", owner=owner)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.patch(
            f"/workspaces/{ws.id}/memberships/{uuid4()}",
            json={"role": "viewer"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


# --- DELETE -----------------------------------------------------------------


async def test_delete_owner_removes_editor(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="owner-del@example.com")
    editor = await _seed_user(session, email="editor-del@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-del", owner=owner)
    member = Membership(workspace_id=ws.id, user_id=editor.id, role=WorkspaceRole.EDITOR)
    session.add(member)
    await session.flush()
    member_id = member.id

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.delete(
            f"/workspaces/{ws.id}/memberships/{editor.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 204

    # Membership row gone.
    await session.commit()
    gone = (
        await session.execute(select(Membership).where(Membership.id == member_id))
    ).scalar_one_or_none()
    assert gone is None
    # Audit row stays under same resource_id.
    rows = await _audit_rows(session, member_id)
    assert [r.action for r in rows] == ["membership.delete"]
    assert rows[0].before_json == {"user_id": str(editor.id), "role": "editor"}


async def test_delete_last_owner_returns_409(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="lonely-del@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-lonely-del", owner=owner)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.delete(
            f"/workspaces/{ws.id}/memberships/{owner.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409
    assert "last Owner" in resp.json()["detail"]
    # Membership still exists.
    still = (
        await session.execute(
            select(Membership).where(
                Membership.workspace_id == ws.id, Membership.user_id == owner.id
            )
        )
    ).scalar_one()
    assert still.role is WorkspaceRole.OWNER


async def test_delete_self_when_other_owner_exists_allowed(
    session: AsyncSession,
) -> None:
    owner_a = await _seed_user(session, email="leaving@example.com")
    owner_b = await _seed_user(session, email="staying@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-leave", owner=owner_a)
    session.add(Membership(workspace_id=ws.id, user_id=owner_b.id, role=WorkspaceRole.OWNER))
    await session.flush()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner_a.email)
        resp = await client.delete(
            f"/workspaces/{ws.id}/memberships/{owner_a.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 204


async def test_delete_unknown_returns_404(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="owner-d404@example.com")
    ws = await _seed_workspace_with_owner(session, slug="memb-d404", owner=owner)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.delete(
            f"/workspaces/{ws.id}/memberships/{uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404
