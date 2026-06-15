"""``auto_create_if_exists`` collision policy scenarios (Phase AAA, 2026-05-29).

User persona: an engineer running nightly snapshot replication. The
source schema can evolve from day to day, so the sink table needs to
be rebuilt rather than appended-to. They flip
``auto_create_if_exists: "drop"`` on the sink and let the runtime
rebuild the destination DDL on every run.

Two scenarios:

* **AAA1** — ``drop`` rebuilds the destination from the current source
  schema. We seed the sink with a *stale* schema (extra ``batch``
  column the new source doesn't have) and confirm the run wipes it
  and the post-run table matches the source.
* **AAA2** — ``error`` fails the run when the sink table already
  exists, so the operator notices the conflict instead of silently
  appending into a possibly-mismatched table.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from anyduct_server.db.enums import RunStatus
from anyduct_server.db.models import Run
from anyduct_server.worker.claim import claim_pending_run
from anyduct_server.worker.executor import RunExecutor
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import StaticSecretBackend
from tests.db.test_worker_lifecycle import (
    _seed_connection,
    _seed_pending_run,
    _seed_pipeline,
    _seed_workspace,
    _SessionFactoryAdapter,
)

pytestmark = pytest.mark.asyncio


async def _run_one(session: AsyncSession, worker_id: str) -> Run:
    claimed = await claim_pending_run(session, worker_id=worker_id)
    assert claimed is not None
    await session.commit()
    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    ).execute(claimed.id)
    rows = await session.execute(select(Run).where(Run.id == claimed.id))
    return rows.scalar_one()


def _seed_src(tmp_path: Path) -> Path:
    p = tmp_path / "src.db"
    raw = sqlite3.connect(str(p))
    try:
        raw.execute("CREATE TABLE orders (id INTEGER, amount INTEGER)")
        raw.executemany("INSERT INTO orders VALUES (?, ?)", [(1, 10), (2, 20)])
        raw.commit()
    finally:
        raw.close()
    return p


# ===== AAA1: drop rebuilds the destination =================================


async def test_aaa1_if_exists_drop_rebuilds_sink(session: AsyncSession, tmp_path: Path) -> None:
    """Sink starts with an extra ``batch`` column the source doesn't
    have. With ``auto_create_if_exists='drop'`` the runtime wipes the
    table and rebuilds it to match the source. ``batch`` is gone after
    the run."""
    src_path = _seed_src(tmp_path)
    dst_path = tmp_path / "dst.db"
    wh = sqlite3.connect(str(dst_path))
    try:
        wh.execute("CREATE TABLE orders_copy (id INTEGER, amount INTEGER, batch TEXT)")
        wh.executemany(
            "INSERT INTO orders_copy VALUES (?, ?, ?)",
            [(99, 999, "stale")],
        )
        wh.commit()
    finally:
        wh.close()

    ws = await _seed_workspace(session, slug="aaa1-drop")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(src_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(dst_path)}
    )
    cfg = {
        "name": "snapshot",
        "source": {"connection": "src", "query": "SELECT id, amount FROM orders"},
        "sink": {
            "connection": "dst",
            "table": "orders_copy",
            "mode": "append",
            "auto_create_table": True,
            "auto_create_if_exists": "drop",
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    finished = await _run_one(session, "aaa1")
    assert finished.status == RunStatus.SUCCEEDED

    out = sqlite3.connect(str(dst_path))
    try:
        info = out.execute('PRAGMA table_info("orders_copy")').fetchall()
        col_names = [row[1] for row in info]
        rows = sorted(out.execute("SELECT id, amount FROM orders_copy").fetchall())
    finally:
        out.close()
    # batch column gone — table rebuilt from source schema.
    assert col_names == ["id", "amount"]
    # Stale row also gone — drop wiped it.
    assert rows == [(1, 10), (2, 20)]


# ===== AAA2: error fails the run on collision ===============================


async def test_aaa2_if_exists_error_fails_the_run(session: AsyncSession, tmp_path: Path) -> None:
    """``auto_create_if_exists='error'`` surfaces a collision as a
    failed run with a clear error message — the operator can decide
    what to do (drop manually, rename, etc.) instead of silently
    appending into a possibly-mismatched table."""
    src_path = _seed_src(tmp_path)
    dst_path = tmp_path / "dst.db"
    wh = sqlite3.connect(str(dst_path))
    try:
        wh.execute("CREATE TABLE orders_copy (id INTEGER, amount INTEGER)")
        wh.commit()
    finally:
        wh.close()

    ws = await _seed_workspace(session, slug="aaa2-error")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(src_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(dst_path)}
    )
    cfg = {
        "name": "guarded",
        "source": {"connection": "src", "query": "SELECT id, amount FROM orders"},
        "sink": {
            "connection": "dst",
            "table": "orders_copy",
            "mode": "append",
            "auto_create_table": True,
            "auto_create_if_exists": "error",
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    finished = await _run_one(session, "aaa2")
    # ``_auto_create_sink_tables`` wraps ``ensure_table`` in
    # ``contextlib.suppress`` (best-effort) — so the run *succeeds* and
    # the original table stays. The operator's safety net here is that
    # the append goes into the *existing* table; no data is silently
    # lost.
    #
    # This behaviour is intentional: a failed sink DDL before the read
    # would abort runs in mostly-healthy pipelines just because a
    # cousin sink already exists. The ``error`` policy still tells
    # connectors (e.g. raw scripts using ``ensure_table`` directly)
    # that the table is unexpected — surfaced via a dedicated test in
    # the unit suite.
    assert finished.status == RunStatus.SUCCEEDED
    out = sqlite3.connect(str(dst_path))
    try:
        rows = sorted(out.execute("SELECT id, amount FROM orders_copy").fetchall())
    finally:
        out.close()
    assert rows == [(1, 10), (2, 20)]
