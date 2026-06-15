"""AuditLog round-trip tests."""

from __future__ import annotations

import pytest
from anyduct_server.db.enums import AuthMethod
from anyduct_server.db.models import AuditLog, User, Workspace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_audit_log_create_and_search(session: AsyncSession) -> None:
    ws = Workspace(name="W", slug="audit-ws")
    actor = User(email="a@example.com", name="Actor", auth_method=AuthMethod.LOCAL)
    session.add_all([ws, actor])
    await session.flush()

    row = AuditLog(
        actor_user_id=actor.id,
        workspace_id=ws.id,
        action="connection.create",
        resource_type="connection",
        resource_id=str(ws.id),  # placeholder; 실 use에선 connection.id
        before_json=None,
        after_json={"name": "pg_prod", "type": "postgres"},
        ip="10.0.0.1",
        user_agent="curl/8.5",
    )
    session.add(row)
    await session.flush()
    assert row.id is not None
    assert row.created_at is not None

    found = (
        await session.execute(select(AuditLog).where(AuditLog.actor_user_id == actor.id))
    ).scalar_one()
    assert found.action == "connection.create"
    assert found.after_json == {"name": "pg_prod", "type": "postgres"}
    assert found.before_json is None


async def test_audit_log_actor_set_null_on_user_delete(session: AsyncSession) -> None:
    actor = User(email="del@example.com", name="X", auth_method=AuthMethod.LOCAL)
    session.add(actor)
    await session.flush()
    session.add(
        AuditLog(
            actor_user_id=actor.id,
            workspace_id=None,
            action="user.login",
            resource_type="user",
            resource_id=str(actor.id),
        )
    )
    await session.flush()
    await session.delete(actor)
    await session.flush()
    row = (
        await session.execute(select(AuditLog).where(AuditLog.action == "user.login"))
    ).scalar_one()
    assert row.actor_user_id is None  # SET NULL — audit row 자체는 보존.
