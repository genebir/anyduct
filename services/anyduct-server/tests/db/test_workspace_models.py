"""Workspace / User / Membership / PAT round-trip tests."""

from __future__ import annotations

import pytest
from anyduct_server.db.enums import AuthMethod, WorkspaceRole
from anyduct_server.db.models import Membership, PersonalAccessToken, User, Workspace
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_workspace_create_and_lookup(session: AsyncSession) -> None:
    ws = Workspace(name="Marketing", slug="marketing")
    session.add(ws)
    await session.flush()
    assert ws.id is not None
    assert ws.created_at is not None
    assert ws.color_hex == "#FF3D8B"

    found = (
        await session.execute(select(Workspace).where(Workspace.slug == "marketing"))
    ).scalar_one()
    assert found.id == ws.id


async def test_workspace_slug_is_unique(session: AsyncSession) -> None:
    session.add(Workspace(name="Marketing", slug="marketing"))
    session.add(Workspace(name="Marketing Two", slug="marketing"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_user_local_vs_oidc(session: AsyncSession) -> None:
    local = User(email="a@example.com", name="A", auth_method=AuthMethod.LOCAL, password_hash="x")
    oidc = User(email="b@example.com", name="B", auth_method=AuthMethod.OIDC_GOOGLE)
    session.add_all([local, oidc])
    await session.flush()
    assert local.password_hash == "x"
    assert oidc.password_hash is None


async def test_membership_uniqueness(session: AsyncSession) -> None:
    ws = Workspace(name="W", slug="w")
    u = User(email="m@example.com", name="M", auth_method=AuthMethod.LOCAL)
    session.add_all([ws, u])
    await session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=u.id, role=WorkspaceRole.OWNER))
    await session.flush()
    # 같은 (workspace, user)에 두 번째 membership은 unique 위반.
    session.add(Membership(workspace_id=ws.id, user_id=u.id, role=WorkspaceRole.EDITOR))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_membership_cascade_on_workspace_delete(session: AsyncSession) -> None:
    ws = Workspace(name="W", slug="w-del")
    u = User(email="del@example.com", name="D", auth_method=AuthMethod.LOCAL)
    session.add_all([ws, u])
    await session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=u.id, role=WorkspaceRole.VIEWER))
    await session.flush()
    await session.delete(ws)
    await session.flush()
    remaining = (
        (await session.execute(select(Membership).where(Membership.user_id == u.id)))
        .scalars()
        .all()
    )
    assert remaining == []


async def test_pat_prefix_unique(session: AsyncSession) -> None:
    u = User(email="p@example.com", name="P", auth_method=AuthMethod.LOCAL)
    session.add(u)
    await session.flush()
    session.add(
        PersonalAccessToken(user_id=u.id, name="ci", prefix="anyduct_pat_abcd1234", token_hash="h1")
    )
    session.add(
        PersonalAccessToken(
            user_id=u.id, name="laptop", prefix="anyduct_pat_abcd1234", token_hash="h2"
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
