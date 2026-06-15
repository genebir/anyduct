"""Cross-DB upsert + auto_create_table — engineer persona (Phase AAC).

The persona is the same engineer from Phase AAB but now they want a
*live* customers cache instead of a daily snapshot. Each run pulls
the current customers table and **upserts** by ``id`` — existing
rows update, new rows insert. The first run still needs the sink
table created automatically (``auto_create_table: true``).

If ``ensure_table`` doesn't emit a uniqueness constraint on the
``key_columns``, the very first run's UPSERT raises *"ON CONFLICT
clause does not match any PRIMARY KEY or UNIQUE constraint"* —
that's exactly the silent failure dogfooding is supposed to catch.

Two-pass flow:

* Day 1 — empty sink; ``auto_create_table`` creates customers with a
  unique constraint on ``id``; upsert inserts both rows.
* Day 2 — same sink; one row has updated values, one is new;
  upsert merges them. Total = 3 (not 4 — no duplicate id=1).
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


async def _drain_one(session: AsyncSession, worker_id: str) -> Run:
    claimed = await claim_pending_run(session, worker_id=worker_id)
    assert claimed is not None
    await session.commit()
    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    ).execute(claimed.id)
    rows = await session.execute(select(Run).where(Run.id == claimed.id))
    return rows.scalar_one()


def _seed_source(src_path: Path, rows: list[tuple[int, str, int]]) -> None:
    raw = sqlite3.connect(str(src_path))
    try:
        raw.execute("DROP TABLE IF EXISTS customers")
        raw.execute("CREATE TABLE customers (id INTEGER, name TEXT, balance INTEGER)")
        raw.executemany("INSERT INTO customers VALUES (?, ?, ?)", rows)
        raw.commit()
    finally:
        raw.close()


async def test_aac1_upsert_with_auto_create_merges_across_runs(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Day-1 creates the sink with a uniqueness constraint on ``id``
    (so day-1's own UPSERT succeeds). Day-2 changes one row's balance
    and adds a new id; the result is 3 rows total, with id=1's
    balance updated."""
    src_path = tmp_path / "live.db"
    dst_path = tmp_path / "cache.db"

    ws = await _seed_workspace(session, slug="aac1-upsert")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(src_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(dst_path)}
    )
    cfg = {
        "name": "customers_cache",
        "source": {
            "connection": "src",
            "query": "SELECT id, name, balance FROM customers",
        },
        "sink": {
            "connection": "dst",
            "table": "customers_cache",
            "mode": "upsert",
            "key_columns": ["id"],
            "auto_create_table": True,
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="cache", config=cfg)

    # Day 1 — bootstrap.
    _seed_source(src_path, [(1, "Alice", 100), (2, "Bob", 200)])
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    day1 = await _drain_one(session, "aac1-day1")
    assert day1.status == RunStatus.SUCCEEDED, (
        f"day-1 failed: {day1.error_class}: {day1.error_message}"
    )

    out1 = sqlite3.connect(str(dst_path))
    try:
        rows1 = sorted(out1.execute("SELECT id, name, balance FROM customers_cache").fetchall())
    finally:
        out1.close()
    assert rows1 == [(1, "Alice", 100), (2, "Bob", 200)]

    # Day 2 — Alice's balance moved; Carol joined; Bob is unchanged.
    _seed_source(
        src_path,
        [(1, "Alice", 150), (2, "Bob", 200), (3, "Carol", 300)],
    )
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    day2 = await _drain_one(session, "aac1-day2")
    assert day2.status == RunStatus.SUCCEEDED, (
        f"day-2 failed: {day2.error_class}: {day2.error_message}"
    )

    out2 = sqlite3.connect(str(dst_path))
    try:
        rows2 = sorted(out2.execute("SELECT id, name, balance FROM customers_cache").fetchall())
    finally:
        out2.close()
    # Three rows total, Alice updated in place — no duplicate id=1.
    assert rows2 == [(1, "Alice", 150), (2, "Bob", 200), (3, "Carol", 300)]
