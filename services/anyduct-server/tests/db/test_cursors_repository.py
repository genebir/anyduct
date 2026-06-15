"""Integration tests for ``CursorRepository`` + the ``cursors`` table
(Step 6.1 — DB-backed CursorState)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from anyduct_server.cursors import CursorRepository
from anyduct_server.db.models import Workspace
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _seed_workspace(session: AsyncSession, *, slug: str) -> Workspace:
    ws = Workspace(name=slug.upper(), slug=slug, color_hex="#000000")
    session.add(ws)
    await session.flush()
    return ws


async def test_get_missing_returns_none(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="cursors-test-1")
    repo = CursorRepository(session)
    got = await repo.get(workspace_id=ws.id, name="never-set")
    assert got is None


async def test_upsert_inserts_new_row(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="cursors-test-2")
    repo = CursorRepository(session)
    row = await repo.upsert(
        workspace_id=ws.id,
        name="orders",
        cursor_column="id",
        cursor_value=42,
    )
    assert row.cursor_column == "id"
    assert row.cursor_value == 42

    again = await repo.get(workspace_id=ws.id, name="orders")
    assert again is not None
    assert again.cursor_value == 42


async def test_upsert_updates_existing_row(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="cursors-test-3")
    repo = CursorRepository(session)
    await repo.upsert(workspace_id=ws.id, name="orders", cursor_column="id", cursor_value=1)
    await repo.upsert(workspace_id=ws.id, name="orders", cursor_column="id", cursor_value=99)

    rows = await repo.list_for_workspace(workspace_id=ws.id)
    assert len(rows) == 1
    assert rows[0].cursor_value == 99


async def test_upsert_handles_jsonable_values(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="cursors-test-4")
    repo = CursorRepository(session)

    # Strings, floats, bools, and ISO-stamped datetimes all need to
    # round-trip without a type-discriminator column.
    cases: list[tuple[str, object]] = [
        ("int", 7),
        ("string", "abc"),
        ("float", 3.14),
        ("bool", True),
        ("datetime", datetime(2026, 5, 19, 3, 0, tzinfo=UTC).isoformat()),
        ("none", None),
    ]
    for name, value in cases:
        await repo.upsert(workspace_id=ws.id, name=name, cursor_column="c", cursor_value=value)
    for name, value in cases:
        row = await repo.get(workspace_id=ws.id, name=name)
        assert row is not None, f"{name!r} not found"
        assert row.cursor_value == value


async def test_delete_removes_row(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="cursors-test-5")
    repo = CursorRepository(session)
    await repo.upsert(workspace_id=ws.id, name="orders", cursor_column="id", cursor_value=1)

    deleted = await repo.delete(workspace_id=ws.id, name="orders")
    assert deleted is True
    assert await repo.get(workspace_id=ws.id, name="orders") is None


async def test_delete_missing_returns_false(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="cursors-test-6")
    repo = CursorRepository(session)
    deleted = await repo.delete(workspace_id=ws.id, name="never-existed")
    assert deleted is False


async def test_same_name_different_workspaces_are_distinct(
    session: AsyncSession,
) -> None:
    ws_a = await _seed_workspace(session, slug="cursors-test-7a")
    ws_b = await _seed_workspace(session, slug="cursors-test-7b")
    repo = CursorRepository(session)
    await repo.upsert(workspace_id=ws_a.id, name="orders", cursor_column="id", cursor_value=1)
    await repo.upsert(workspace_id=ws_b.id, name="orders", cursor_column="id", cursor_value=99)

    in_a = await repo.get(workspace_id=ws_a.id, name="orders")
    in_b = await repo.get(workspace_id=ws_b.id, name="orders")
    assert in_a is not None and in_a.cursor_value == 1
    assert in_b is not None and in_b.cursor_value == 99


async def test_list_for_workspace_orders_by_name(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="cursors-test-8")
    repo = CursorRepository(session)
    for n in ("c", "a", "b"):
        await repo.upsert(workspace_id=ws.id, name=n, cursor_column="id", cursor_value=1)

    rows = await repo.list_for_workspace(workspace_id=ws.id)
    assert [r.name for r in rows] == ["a", "b", "c"]


async def test_workspace_delete_cascades_to_cursors(session: AsyncSession) -> None:
    """ON DELETE CASCADE on the FK should drop all of the workspace's cursors."""
    ws = await _seed_workspace(session, slug="cursors-test-9")
    repo = CursorRepository(session)
    await repo.upsert(workspace_id=ws.id, name="x", cursor_column="id", cursor_value=1)

    await session.delete(ws)
    await session.flush()

    rows = await repo.list_for_workspace(workspace_id=ws.id)
    assert rows == []
