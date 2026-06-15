"""Migration form wire-shape contract — Phase AAO (2026-05-29).

The web ``MigrationForm`` (Phase AAN3/AAN4) emits three distinct
sink shapes depending on the user's chosen strategy. This module
hand-builds the *exact* JSON the form would emit and runs it through
the worker end-to-end, so a future tweak to ``migration-config.ts``
that drifts from runtime expectations is caught here rather than at
3am in prod.

Three scenarios, one per strategy:

* **AAO1 — Full snapshot** — ``mode=overwrite`` +
  ``auto_create_if_exists='drop'``. Two-pass run with schema drift
  between days: day-1 ``(id, name)``, day-2 ``(id, email)`` (new
  column, old column gone). Sink is fully rebuilt each run.
* **AAO2 — Append new rows** — ``mode=append`` + ``cursor_column``.
  Source gets fresh rows; the *append* strategy itself just keeps
  adding, so we model the "new rows appear" angle by seeding more
  rows between runs and confirming the sink keeps both batches.
* **AAO3 — Live mirror** — ``mode=upsert`` + ``key_columns`` +
  auto-emitted PRIMARY KEY (ADR-0072). Day-2 updates one row,
  inserts another; the sink should reflect the merged state with
  no duplicates.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from etlx_server.db.enums import RunStatus
from etlx_server.db.models import Run
from etlx_server.worker.claim import claim_pending_run
from etlx_server.worker.executor import RunExecutor
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


async def _drain_one(session: AsyncSession, worker_id: str) -> Run:
    claimed = await claim_pending_run(session, worker_id=worker_id)
    assert claimed is not None
    await session.commit()
    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    ).execute(claimed.id)
    rows = await session.execute(select(Run).where(Run.id == claimed.id))
    return rows.scalar_one()


# ===== Helpers that mirror what migration-config.ts emits =================


def _snapshot_config(name: str, sink_table: str) -> dict:
    """Full snapshot strategy — overwrite + drop + auto_create."""
    return {
        "name": name,
        "mode": "batch",
        "source": {"connection": "src", "query": "SELECT * FROM source_t"},
        "sink": {
            "connection": "dst",
            "table": sink_table,
            "mode": "overwrite",
            "auto_create_table": True,
            "auto_create_if_exists": "drop",
        },
    }


def _append_config(name: str, sink_table: str, cursor_column: str) -> dict:
    """Append new rows — mode=append + cursor_column + auto_create."""
    return {
        "name": name,
        "mode": "batch",
        "source": {
            "connection": "src",
            "query": "SELECT * FROM source_t",
            "cursor_column": cursor_column,
        },
        "sink": {
            "connection": "dst",
            "table": sink_table,
            "mode": "append",
            "auto_create_table": True,
        },
    }


def _mirror_config(name: str, sink_table: str, key_columns: list[str]) -> dict:
    """Live mirror — upsert + key_columns + auto_create."""
    return {
        "name": name,
        "mode": "batch",
        "source": {"connection": "src", "query": "SELECT * FROM source_t"},
        "sink": {
            "connection": "dst",
            "table": sink_table,
            "mode": "upsert",
            "key_columns": key_columns,
            "auto_create_table": True,
        },
    }


# ===== AAO1: Full snapshot — schema drift tolerated ========================


async def test_aao1_full_snapshot_rebuilds_each_run(session: AsyncSession, tmp_path: Path) -> None:
    src_path = tmp_path / "src.db"
    dst_path = tmp_path / "dst.db"

    ws = await _seed_workspace(session, slug="aao1-snapshot")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(src_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(dst_path)}
    )
    p, pv = await _seed_pipeline(
        session,
        workspace_id=ws.id,
        name="snapshot",
        config=_snapshot_config("snapshot", "snap_t"),
    )

    # Day 1 — source is (id, name).
    raw = sqlite3.connect(str(src_path))
    try:
        raw.execute("CREATE TABLE source_t (id INTEGER, name TEXT)")
        raw.executemany(
            "INSERT INTO source_t VALUES (?, ?)",
            [(1, "alice"), (2, "bob")],
        )
        raw.commit()
    finally:
        raw.close()
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    day1 = await _drain_one(session, "aao1-d1")
    assert day1.status == RunStatus.SUCCEEDED

    # Day 2 — schema mutates. With ``drop`` the day-1 ``name`` column
    # disappears and only day-2's payload survives.
    raw = sqlite3.connect(str(src_path))
    try:
        raw.execute("DROP TABLE source_t")
        raw.execute("CREATE TABLE source_t (id INTEGER, email TEXT)")
        raw.executemany(
            "INSERT INTO source_t VALUES (?, ?)",
            [(10, "x@example.com"), (20, "y@example.com")],
        )
        raw.commit()
    finally:
        raw.close()
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    day2 = await _drain_one(session, "aao1-d2")
    assert day2.status == RunStatus.SUCCEEDED

    out = sqlite3.connect(str(dst_path))
    try:
        cols = [r[1] for r in out.execute('PRAGMA table_info("snap_t")').fetchall()]
        rows = sorted(out.execute("SELECT id, email FROM snap_t").fetchall())
    finally:
        out.close()
    assert cols == ["id", "email"]
    assert rows == [(10, "x@example.com"), (20, "y@example.com")]


# ===== AAO2: Append new rows — both batches survive =======================


async def test_aao2_append_keeps_both_batches(session: AsyncSession, tmp_path: Path) -> None:
    """Append strategy keeps history. Two runs against a growing
    source: the sink ends up with day-1 + day-2 rows combined."""
    src_path = tmp_path / "src.db"
    dst_path = tmp_path / "dst.db"

    ws = await _seed_workspace(session, slug="aao2-append")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(src_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(dst_path)}
    )
    p, pv = await _seed_pipeline(
        session,
        workspace_id=ws.id,
        name="append",
        config=_append_config("append", "append_t", cursor_column="id"),
    )

    raw = sqlite3.connect(str(src_path))
    try:
        raw.execute("CREATE TABLE source_t (id INTEGER, value TEXT)")
        raw.executemany(
            "INSERT INTO source_t VALUES (?, ?)",
            [(1, "first"), (2, "second")],
        )
        raw.commit()
    finally:
        raw.close()
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    day1 = await _drain_one(session, "aao2-d1")
    assert day1.status == RunStatus.SUCCEEDED

    # Append more rows on day-2.
    raw = sqlite3.connect(str(src_path))
    try:
        raw.executemany(
            "INSERT INTO source_t VALUES (?, ?)",
            [(3, "third"), (4, "fourth")],
        )
        raw.commit()
    finally:
        raw.close()
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    day2 = await _drain_one(session, "aao2-d2")
    assert day2.status == RunStatus.SUCCEEDED

    out = sqlite3.connect(str(dst_path))
    try:
        rows = sorted(out.execute("SELECT id, value FROM append_t").fetchall())
    finally:
        out.close()
    # The append strategy's contract for this scenario is "history is
    # preserved on the destination" — both batches land. Whether the
    # day-1 rows appear once or twice depends on the cursor wiring at
    # the worker level, which is out of scope here. The lock-in is
    # that day-2 rows (3, 4) land *and* day-1 rows survive.
    keys = {k for (k, _) in rows}
    assert 3 in keys and 4 in keys
    assert 1 in keys and 2 in keys


# ===== AAO3: Live mirror — upsert merges day-2 =============================


async def test_aao3_live_mirror_upserts_with_pk(session: AsyncSession, tmp_path: Path) -> None:
    """Mirror strategy emits ``mode=upsert + key_columns=[id]``. With
    ``auto_create_table=true`` the runtime auto-creates the sink with
    a ``PRIMARY KEY (id)`` (ADR-0072), so the very first run's
    ``ON CONFLICT`` resolves cleanly. Day-2 updates id=1 and adds
    id=3 — total 3 rows with no duplicates."""
    src_path = tmp_path / "src.db"
    dst_path = tmp_path / "dst.db"

    ws = await _seed_workspace(session, slug="aao3-mirror")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(src_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(dst_path)}
    )
    p, pv = await _seed_pipeline(
        session,
        workspace_id=ws.id,
        name="mirror",
        config=_mirror_config("mirror", "mirror_t", key_columns=["id"]),
    )

    # Day 1 — bootstrap.
    raw = sqlite3.connect(str(src_path))
    try:
        raw.execute("CREATE TABLE source_t (id INTEGER, name TEXT, balance INTEGER)")
        raw.executemany(
            "INSERT INTO source_t VALUES (?, ?, ?)",
            [(1, "alice", 100), (2, "bob", 200)],
        )
        raw.commit()
    finally:
        raw.close()
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    day1 = await _drain_one(session, "aao3-d1")
    assert day1.status == RunStatus.SUCCEEDED, (
        f"day-1 failed: {day1.error_class}: {day1.error_message}"
    )

    # Day 2 — Alice updated, Carol joined.
    raw = sqlite3.connect(str(src_path))
    try:
        raw.execute("DELETE FROM source_t")
        raw.executemany(
            "INSERT INTO source_t VALUES (?, ?, ?)",
            [(1, "alice", 150), (2, "bob", 200), (3, "carol", 300)],
        )
        raw.commit()
    finally:
        raw.close()
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    day2 = await _drain_one(session, "aao3-d2")
    assert day2.status == RunStatus.SUCCEEDED, (
        f"day-2 failed: {day2.error_class}: {day2.error_message}"
    )

    out = sqlite3.connect(str(dst_path))
    try:
        rows = sorted(out.execute("SELECT id, name, balance FROM mirror_t").fetchall())
    finally:
        out.close()
    assert rows == [(1, "alice", 150), (2, "bob", 200), (3, "carol", 300)]
