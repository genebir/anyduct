"""AuditService integration tests (Step 8.4)."""

from __future__ import annotations

import pytest
from anyduct_server.audit.service import AuditService, RequestMeta
from anyduct_server.db.enums import AuthMethod, WorkspaceRole
from anyduct_server.db.models import AuditLog, Membership, User, Workspace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _seed_actor_and_workspace(
    session: AsyncSession,
) -> tuple[User, Workspace]:
    user = User(
        email="actor@example.com",
        name="Actor",
        auth_method=AuthMethod.LOCAL,
        password_hash="x" * 60,
    )
    ws = Workspace(name="Demo", slug="demo-audit-svc", color_hex="#FF3D8B")
    session.add_all([user, ws])
    await session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=user.id, role=WorkspaceRole.OWNER))
    await session.flush()
    return user, ws


async def test_record_writes_row_with_meta(session: AsyncSession) -> None:
    user, ws = await _seed_actor_and_workspace(session)
    svc = AuditService(session, request_meta=RequestMeta(ip="10.0.0.1", user_agent="pytest/1.0"))
    row = await svc.record(
        actor_user_id=user.id,
        workspace_id=ws.id,
        action="workspace.update",
        resource_type="workspace",
        resource_id=str(ws.id),
        before={"color_hex": "#FF3D8B"},
        after={"color_hex": "#000000"},
    )
    assert row.id is not None
    assert row.created_at is not None
    assert row.ip == "10.0.0.1"
    assert row.user_agent == "pytest/1.0"
    # Roundtrip via the session.
    fetched = (await session.execute(select(AuditLog).where(AuditLog.id == row.id))).scalar_one()
    assert fetched.action == "workspace.update"
    assert fetched.before_json == {"color_hex": "#FF3D8B"}
    assert fetched.after_json == {"color_hex": "#000000"}


async def test_record_without_meta_uses_nulls(session: AsyncSession) -> None:
    user, ws = await _seed_actor_and_workspace(session)
    svc = AuditService(session)  # no RequestMeta
    row = await svc.record(
        actor_user_id=user.id,
        workspace_id=ws.id,
        action="x.y",
        resource_type="x",
    )
    assert row.ip is None
    assert row.user_agent is None
    assert row.resource_id is None
    assert row.before_json is None
    assert row.after_json is None


async def test_record_allows_null_actor_and_workspace(session: AsyncSession) -> None:
    """System events (no actor, no workspace) are valid — e.g. boot, GC."""
    svc = AuditService(session)
    row = await svc.record(
        actor_user_id=None,
        workspace_id=None,
        action="system.boot",
        resource_type="system",
    )
    assert row.actor_user_id is None
    assert row.workspace_id is None


async def test_record_rolls_back_with_caller_transaction(session: AsyncSession) -> None:
    """Audit rows must follow the caller's txn boundary — verify rollback path."""
    user, ws = await _seed_actor_and_workspace(session)
    svc = AuditService(session)
    row = await svc.record(
        actor_user_id=user.id,
        workspace_id=ws.id,
        action="x.create",
        resource_type="x",
    )
    row_id = row.id
    # Caller decides to roll back its work — savepoint rollback drops the audit row too.
    await session.rollback()
    after_rollback = (
        await session.execute(select(AuditLog).where(AuditLog.id == row_id))
    ).scalar_one_or_none()
    assert after_rollback is None
