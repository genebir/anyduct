"""Integration tests for :class:`DbCursorState` — the sync facade over the
``cursors`` table (Step 6.1).

The sync API is what plugs into the core :class:`CursorState` ABC, so we
exercise it from inside ``asyncio.to_thread`` exactly like a production
worker would (Pipeline.run runs in a thread; from that thread there's no
running asyncio loop, so a sync SQLAlchemy/psycopg engine is the
straightforward path).

A *fresh sync* engine (psycopg, not asyncpg) is built per test from the
same Postgres URL the rest of the server uses — asyncpg pools are bound
to their creating event loop, but a sync engine has no such constraint
and can be driven from any thread. State is hand-cleaned in ``finally``
so later tests in the session aren't affected.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from etlx_server.cursors import DbCursorState
from etlx_server.db.models import Cursor as CursorRow
from etlx_server.db.models import Workspace
from sqlalchemy import Engine, create_engine, delete
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from etl_plugins.core.cursor import Cursor

pytestmark = pytest.mark.asyncio


async def _make_workspace(engine: AsyncEngine, slug: str) -> Workspace:
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        ws = Workspace(name=slug.upper(), slug=slug, color_hex="#000000")
        s.add(ws)
        await s.commit()
        await s.refresh(ws)
        return ws


async def _drop_workspace(engine: AsyncEngine, workspace_id: object) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        await s.execute(delete(CursorRow).where(CursorRow.workspace_id == workspace_id))
        await s.execute(delete(Workspace).where(Workspace.id == workspace_id))
        await s.commit()


@pytest.fixture
def sync_engine(metadata_db_url: str) -> Engine:
    """Sync SQLAlchemy engine for DbCursorState.

    Swaps ``+asyncpg`` for ``+psycopg`` so SQLAlchemy picks psycopg v3
    (the bare ``postgresql://`` default is psycopg2, which isn't installed).
    """
    return create_engine(metadata_db_url.replace("+asyncpg", "+psycopg"), future=True)


async def test_get_missing_returns_none(metadata_engine: AsyncEngine, sync_engine: Engine) -> None:
    ws = await _make_workspace(metadata_engine, "cursorstate-1")
    try:
        state = DbCursorState(sync_engine, workspace_id=ws.id)
        got = await asyncio.to_thread(state.get, "never-set")
        assert got is None
    finally:
        await _drop_workspace(metadata_engine, ws.id)


async def test_set_then_get_round_trip(metadata_engine: AsyncEngine, sync_engine: Engine) -> None:
    ws = await _make_workspace(metadata_engine, "cursorstate-2")
    try:
        state = DbCursorState(sync_engine, workspace_id=ws.id)
        await asyncio.to_thread(state.set, "orders", Cursor(column="id", value=42))
        got = await asyncio.to_thread(state.get, "orders")
        assert got is not None
        assert got.column == "id"
        assert got.value == 42
    finally:
        await _drop_workspace(metadata_engine, ws.id)


async def test_set_overwrites(metadata_engine: AsyncEngine, sync_engine: Engine) -> None:
    ws = await _make_workspace(metadata_engine, "cursorstate-3")
    try:
        state = DbCursorState(sync_engine, workspace_id=ws.id)
        await asyncio.to_thread(state.set, "orders", Cursor(column="id", value=1))
        await asyncio.to_thread(state.set, "orders", Cursor(column="id", value=99))
        got = await asyncio.to_thread(state.get, "orders")
        assert got is not None and got.value == 99
    finally:
        await _drop_workspace(metadata_engine, ws.id)


async def test_update_helper(metadata_engine: AsyncEngine, sync_engine: Engine) -> None:
    ws = await _make_workspace(metadata_engine, "cursorstate-4")
    try:
        state = DbCursorState(sync_engine, workspace_id=ws.id)
        cur = await asyncio.to_thread(state.update, "orders", "id", 7)
        assert cur == Cursor(column="id", value=7)
        got = await asyncio.to_thread(state.get, "orders")
        assert got == cur
    finally:
        await _drop_workspace(metadata_engine, ws.id)


async def test_delete_removes_then_get_returns_none(
    metadata_engine: AsyncEngine, sync_engine: Engine
) -> None:
    ws = await _make_workspace(metadata_engine, "cursorstate-5")
    try:
        state = DbCursorState(sync_engine, workspace_id=ws.id)
        await asyncio.to_thread(state.set, "orders", Cursor(column="id", value=1))
        await asyncio.to_thread(state.delete, "orders")
        got = await asyncio.to_thread(state.get, "orders")
        assert got is None
    finally:
        await _drop_workspace(metadata_engine, ws.id)


async def test_delete_missing_is_a_noop(metadata_engine: AsyncEngine, sync_engine: Engine) -> None:
    ws = await _make_workspace(metadata_engine, "cursorstate-6")
    try:
        state = DbCursorState(sync_engine, workspace_id=ws.id)
        await asyncio.to_thread(state.delete, "never-existed")  # must not raise
    finally:
        await _drop_workspace(metadata_engine, ws.id)


async def test_datetime_round_trips_as_iso(
    metadata_engine: AsyncEngine, sync_engine: Engine
) -> None:
    ws = await _make_workspace(metadata_engine, "cursorstate-7")
    try:
        state = DbCursorState(sync_engine, workspace_id=ws.id)
        ts = datetime(2026, 5, 19, 3, 0, tzinfo=UTC)
        await asyncio.to_thread(state.set, "events", Cursor(column="ts", value=ts))
        got = await asyncio.to_thread(state.get, "events")
        assert got is not None
        # ISO-8601 lexicographic order matches chronological order — the
        # core docstring guarantees this is safe for the strict-`>` cursor.
        assert got.value == ts.isoformat()
    finally:
        await _drop_workspace(metadata_engine, ws.id)


async def test_workspaces_dont_collide_on_same_name(
    metadata_engine: AsyncEngine, sync_engine: Engine
) -> None:
    ws_a = await _make_workspace(metadata_engine, "cursorstate-8a")
    ws_b = await _make_workspace(metadata_engine, "cursorstate-8b")
    try:
        state_a = DbCursorState(sync_engine, workspace_id=ws_a.id)
        state_b = DbCursorState(sync_engine, workspace_id=ws_b.id)
        await asyncio.to_thread(state_a.set, "orders", Cursor(column="id", value=1))
        await asyncio.to_thread(state_b.set, "orders", Cursor(column="id", value=99))

        got_a = await asyncio.to_thread(state_a.get, "orders")
        got_b = await asyncio.to_thread(state_b.get, "orders")
        assert got_a is not None and got_a.value == 1
        assert got_b is not None and got_b.value == 99
    finally:
        await _drop_workspace(metadata_engine, ws_a.id)
        await _drop_workspace(metadata_engine, ws_b.id)


async def test_from_url_builds_own_engine(
    metadata_engine: AsyncEngine, metadata_db_url: str
) -> None:
    ws = await _make_workspace(metadata_engine, "cursorstate-9")
    try:
        state = DbCursorState.from_url(metadata_db_url, workspace_id=ws.id)
        await asyncio.to_thread(state.set, "orders", Cursor(column="id", value=5))
        got = await asyncio.to_thread(state.get, "orders")
        assert got is not None and got.value == 5
    finally:
        await _drop_workspace(metadata_engine, ws.id)
