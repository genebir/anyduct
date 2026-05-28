"""AuditLogRepository query integration tests (Step 8.4)."""

from __future__ import annotations

import asyncio

import pytest
from etlx_server.audit.repository import AuditLogRepository
from etlx_server.audit.service import AuditService
from etlx_server.db.enums import AuthMethod, WorkspaceRole
from etlx_server.db.models import Membership, User, Workspace
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _seed(
    session: AsyncSession,
) -> tuple[User, User, Workspace, Workspace]:
    alice = User(
        email="alice@example.com",
        name="Alice",
        auth_method=AuthMethod.LOCAL,
        password_hash="x" * 60,
    )
    bob = User(
        email="bob@example.com", name="Bob", auth_method=AuthMethod.LOCAL, password_hash="x" * 60
    )
    ws_a = Workspace(name="A", slug="ws-a", color_hex="#000000")
    ws_b = Workspace(name="B", slug="ws-b", color_hex="#111111")
    session.add_all([alice, bob, ws_a, ws_b])
    await session.flush()
    session.add_all(
        [
            Membership(workspace_id=ws_a.id, user_id=alice.id, role=WorkspaceRole.OWNER),
            Membership(workspace_id=ws_b.id, user_id=bob.id, role=WorkspaceRole.OWNER),
        ]
    )
    await session.flush()
    return alice, bob, ws_a, ws_b


async def _record(
    session: AsyncSession,
    *,
    actor: User,
    workspace: Workspace | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
) -> None:
    """Helper: record a single audit row, then sleep a hair so created_at differs."""
    await AuditService(session).record(
        actor_user_id=actor.id,
        workspace_id=workspace.id if workspace else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    # Postgres' clock resolution is microseconds, but flushes inside a single
    # txn can share a timestamp. Yield + sleep ensures ordering is observable.
    await asyncio.sleep(0.005)


async def test_query_filters_by_workspace(session: AsyncSession) -> None:
    alice, bob, ws_a, ws_b = await _seed(session)
    await _record(session, actor=alice, workspace=ws_a, action="a.x", resource_type="r")
    await _record(session, actor=bob, workspace=ws_b, action="b.y", resource_type="r")

    repo = AuditLogRepository(session)
    rows = await repo.query(workspace_id=ws_a.id)
    assert [r.action for r in rows] == ["a.x"]


async def test_query_filters_by_actor(session: AsyncSession) -> None:
    alice, bob, ws_a, _ = await _seed(session)
    await _record(session, actor=alice, workspace=ws_a, action="a1", resource_type="r")
    await _record(session, actor=bob, workspace=ws_a, action="b1", resource_type="r")

    repo = AuditLogRepository(session)
    rows = await repo.query(actor_user_id=alice.id)
    assert [r.action for r in rows] == ["a1"]


async def test_query_filters_by_resource_type_and_id(session: AsyncSession) -> None:
    alice, _, ws_a, _ = await _seed(session)
    await _record(
        session, actor=alice, workspace=ws_a, action="x", resource_type="conn", resource_id="c1"
    )
    await _record(
        session, actor=alice, workspace=ws_a, action="y", resource_type="conn", resource_id="c2"
    )
    await _record(
        session, actor=alice, workspace=ws_a, action="z", resource_type="pipe", resource_id="c1"
    )

    repo = AuditLogRepository(session)
    rt_rows = await repo.query(resource_type="conn")
    assert sorted(r.action for r in rt_rows) == ["x", "y"]
    rid_rows = await repo.query(resource_type="conn", resource_id="c1")
    assert [r.action for r in rid_rows] == ["x"]


async def test_query_orders_newest_first(session: AsyncSession) -> None:
    alice, _, ws_a, _ = await _seed(session)
    await _record(session, actor=alice, workspace=ws_a, action="first", resource_type="r")
    await _record(session, actor=alice, workspace=ws_a, action="second", resource_type="r")
    await _record(session, actor=alice, workspace=ws_a, action="third", resource_type="r")

    repo = AuditLogRepository(session)
    rows = await repo.query(workspace_id=ws_a.id)
    assert [r.action for r in rows] == ["third", "second", "first"]


async def test_query_applies_limit_and_offset(session: AsyncSession) -> None:
    alice, _, ws_a, _ = await _seed(session)
    for i in range(5):
        await _record(session, actor=alice, workspace=ws_a, action=f"a{i}", resource_type="r")

    repo = AuditLogRepository(session)
    page1 = await repo.query(workspace_id=ws_a.id, limit=2, offset=0)
    page2 = await repo.query(workspace_id=ws_a.id, limit=2, offset=2)
    assert [r.action for r in page1] == ["a4", "a3"]
    assert [r.action for r in page2] == ["a2", "a1"]


async def test_query_filters_by_action(session: AsyncSession) -> None:
    """Phase U (2026-05-28): action filter scopes the audit feed to one
    exact action name. Used by the UI's "show only run.sql_executed"
    dropdown shortcut."""
    alice, _, ws_a, _ = await _seed(session)
    await _record(
        session,
        actor=alice,
        workspace=ws_a,
        action="run.sql_executed",
        resource_type="run",
    )
    await _record(
        session,
        actor=alice,
        workspace=ws_a,
        action="run.python_executed",
        resource_type="run",
    )
    await _record(session, actor=alice, workspace=ws_a, action="run.trigger", resource_type="run")

    repo = AuditLogRepository(session)
    only_sql = await repo.query(workspace_id=ws_a.id, action="run.sql_executed")
    assert [r.action for r in only_sql] == ["run.sql_executed"]
    only_py = await repo.query(workspace_id=ws_a.id, action="run.python_executed")
    assert [r.action for r in only_py] == ["run.python_executed"]
    none_match = await repo.query(workspace_id=ws_a.id, action="run.does_not_exist")
    assert none_match == []
