"""Connection model round-trip tests."""

from __future__ import annotations

import pytest
from anyduct_server.db.models import Connection, Workspace
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_connection_create_with_jsonb_config(session: AsyncSession) -> None:
    ws = Workspace(name="W", slug="conn-ws")
    session.add(ws)
    await session.flush()
    conn = Connection(
        workspace_id=ws.id,
        type="postgres",
        name="pg_prod",
        config_json={
            "host": "db.internal",
            "port": 5432,
            "database": "app",
            "user": "etl",
            "password": "${SECRET:PG_PASSWORD}",  # placeholder, not the real secret
        },
        secret_refs=["PG_PASSWORD"],
    )
    session.add(conn)
    await session.flush()

    found = (await session.execute(select(Connection).where(Connection.id == conn.id))).scalar_one()
    assert found.type == "postgres"
    assert found.config_json["password"].startswith("${SECRET:")
    assert "PG_PASSWORD" in found.secret_refs


async def test_connection_unique_name_per_workspace(session: AsyncSession) -> None:
    ws = Workspace(name="W", slug="conn-ws2")
    session.add(ws)
    await session.flush()
    session.add(Connection(workspace_id=ws.id, type="postgres", name="dup"))
    session.add(Connection(workspace_id=ws.id, type="mysql", name="dup"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_connection_same_name_different_workspaces_ok(session: AsyncSession) -> None:
    ws1 = Workspace(name="W1", slug="conn-ws3")
    ws2 = Workspace(name="W2", slug="conn-ws4")
    session.add_all([ws1, ws2])
    await session.flush()
    session.add(Connection(workspace_id=ws1.id, type="postgres", name="prod"))
    session.add(Connection(workspace_id=ws2.id, type="postgres", name="prod"))
    await session.flush()  # OK — workspace 격리.
