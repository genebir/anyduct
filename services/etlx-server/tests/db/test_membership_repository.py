"""MembershipRepository integration tests (Step 8.3)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from etlx_server.auth.membership_repository import MembershipRepository
from etlx_server.db.enums import AuthMethod, WorkspaceRole
from etlx_server.db.models import Membership, User, Workspace
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _seed(
    session: AsyncSession,
    *,
    role: WorkspaceRole | None,
    slug: str = "ws",
) -> tuple[Workspace, User]:
    ws = Workspace(name="Demo", slug=slug, color_hex="#FF3D8B")
    user = User(
        email=f"u-{slug}@example.com",
        name="U",
        auth_method=AuthMethod.LOCAL,
        password_hash="x" * 60,
    )
    session.add_all([ws, user])
    await session.flush()
    if role is not None:
        session.add(Membership(workspace_id=ws.id, user_id=user.id, role=role))
        await session.flush()
    return ws, user


async def test_get_role_returns_role_for_member(session: AsyncSession) -> None:
    ws, user = await _seed(session, role=WorkspaceRole.EDITOR)
    repo = MembershipRepository(session)
    assert (await repo.get_role(workspace_id=ws.id, user_id=user.id)) is WorkspaceRole.EDITOR


async def test_get_role_returns_none_for_non_member(session: AsyncSession) -> None:
    ws, _ = await _seed(session, role=WorkspaceRole.OWNER, slug="ws-a")
    other_user = User(
        email="other@example.com",
        name="Other",
        auth_method=AuthMethod.LOCAL,
        password_hash="x" * 60,
    )
    session.add(other_user)
    await session.flush()

    repo = MembershipRepository(session)
    assert await repo.get_role(workspace_id=ws.id, user_id=other_user.id) is None


async def test_get_role_returns_none_for_missing_workspace(
    session: AsyncSession,
) -> None:
    _, user = await _seed(session, role=WorkspaceRole.OWNER, slug="ws-b")
    repo = MembershipRepository(session)
    assert await repo.get_role(workspace_id=uuid4(), user_id=user.id) is None


async def test_get_role_distinguishes_per_workspace(session: AsyncSession) -> None:
    """Same user, two workspaces, different roles — each lookup returns its own."""
    ws_a, user = await _seed(session, role=WorkspaceRole.EDITOR, slug="ws-x")
    ws_b = Workspace(name="B", slug="ws-y", color_hex="#000000")
    session.add(ws_b)
    await session.flush()
    session.add(Membership(workspace_id=ws_b.id, user_id=user.id, role=WorkspaceRole.RUNNER))
    await session.flush()

    repo = MembershipRepository(session)
    assert (await repo.get_role(workspace_id=ws_a.id, user_id=user.id)) is WorkspaceRole.EDITOR
    assert (await repo.get_role(workspace_id=ws_b.id, user_id=user.id)) is WorkspaceRole.RUNNER
