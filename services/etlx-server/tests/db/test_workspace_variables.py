"""WorkspaceVariableRepository — workspace-global variables (ADR-0041, V2)."""

from __future__ import annotations

from etlx_server.db.models import Workspace
from etlx_server.variables.repository import WorkspaceVariableRepository
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_ws(session: AsyncSession, *, slug: str) -> Workspace:
    ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
    session.add(ws)
    await session.flush()
    return ws


async def test_set_creates_then_upserts(session: AsyncSession) -> None:
    ws = await _seed_ws(session, slug="wv-set")
    repo = WorkspaceVariableRepository(session)
    row, created = await repo.set(workspace_id=ws.id, name="tbl", value="seed", description="d")
    assert created is True
    assert row.value_json == "seed"
    assert row.description == "d"

    row2, created2 = await repo.set(workspace_id=ws.id, name="tbl", value="other", description=None)
    assert created2 is False
    assert row2.id == row.id  # upsert, not a new row
    assert row2.value_json == "other"
    assert row2.description is None


async def test_list_as_dict_and_delete(session: AsyncSession) -> None:
    ws = await _seed_ws(session, slug="wv-dict")
    repo = WorkspaceVariableRepository(session)
    await repo.set(workspace_id=ws.id, name="a", value=1, description=None)
    await repo.set(workspace_id=ws.id, name="b", value=[1, 2], description=None)

    assert await repo.as_dict(workspace_id=ws.id) == {"a": 1, "b": [1, 2]}
    rows = await repo.list_for_workspace(workspace_id=ws.id)
    assert [r.name for r in rows] == ["a", "b"]  # ordered by name

    await repo.delete(rows[0])
    assert await repo.as_dict(workspace_id=ws.id) == {"b": [1, 2]}


async def test_get_scoped_to_workspace(session: AsyncSession) -> None:
    ws1 = await _seed_ws(session, slug="wv-w1")
    ws2 = await _seed_ws(session, slug="wv-w2")
    repo = WorkspaceVariableRepository(session)
    await repo.set(workspace_id=ws1.id, name="x", value="one", description=None)

    assert (await repo.get(workspace_id=ws1.id, name="x")) is not None
    assert (await repo.get(workspace_id=ws2.id, name="x")) is None  # different workspace
