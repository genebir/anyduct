"""Dead-letter queue (DLQ) dogfooding scenarios (Phase II, 2026-05-29).

When a transform raises on a particular record and ``dlq:`` is set on
the pipeline, the core routes *that record* to the DLQ sink and keeps
processing the rest — the canonical "partial success" warehouse pattern.

Two scenarios:

* **II1** — record-level routing works at the data plane. With three
  good rows and two bad rows the clean sink ends up with 3 rows and
  the DLQ sink ends up with 2.
* **II2** — the DLQ asset shows up in the catalog. ``derive_lineage``
  must treat the DLQ destination as an output asset just like a
  regular sink. Without this, operators would have no catalog row to
  click when they ask "where did my failed records go?".
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from etlx_server.assets.repository import AssetRepository
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


def _run_one(session: AsyncSession, worker_id: str):  # type: ignore[no-untyped-def]
    """Claim + run the single pending row this scenario seeds."""

    async def _inner() -> Run:
        claimed = await claim_pending_run(session, worker_id=worker_id)
        assert claimed is not None
        await session.commit()
        await RunExecutor(
            _SessionFactoryAdapter(session),
            StaticSecretBackend(),
            worker_id=worker_id,
        ).execute(claimed.id)
        # Re-fetch the row so the test sees the post-run state.
        rows = await session.execute(select(Run).where(Run.id == claimed.id))
        return rows.scalar_one()

    return _inner()


def _query_rows(db_path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(str(db_path))
    try:
        return list(conn.execute(sql).fetchall())
    finally:
        conn.close()


def _seed_orders_warehouse(tmp_path: Path) -> Path:
    """Five rows: three valid (amount > 0), two invalid (amount < 0) — so
    DLQ partitioning is visible at the row count level."""
    db_path = tmp_path / "dlq.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw_orders (id INTEGER, amount INTEGER)")
        conn.executemany(
            "INSERT INTO raw_orders VALUES (?, ?)",
            [
                (1, 100),
                (2, -50),  # bad: negative amount
                (3, 200),
                (4, -75),  # bad: negative amount
                (5, 300),
            ],
        )
        conn.execute("CREATE TABLE clean_orders (id INTEGER, amount INTEGER)")
        conn.execute("CREATE TABLE bad_orders (id INTEGER, amount INTEGER)")
        conn.commit()
    finally:
        conn.close()
    return db_path


# ===== II1: Record-level partial success ====================================


async def test_ii1_dlq_routes_bad_records_keeps_processing_good_ones(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A custom_python transform raises on negative amounts. The DLQ
    routing should send those records to ``bad_orders`` while clean
    rows continue on to ``clean_orders``. The run finishes successfully
    (DLQ doesn't fail the run), and the sinks reflect the partition."""
    db_path = _seed_orders_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="ii1-dlq")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, amount FROM raw_orders"},
        "transforms": [
            {
                "type": "custom_python",
                "code": (
                    "def transform(record):\n"
                    "    if record.data['amount'] < 0:\n"
                    "        raise ValueError('negative amount')\n"
                    "    return record\n"
                ),
            }
        ],
        "sink": {"connection": "dst", "table": "clean_orders", "mode": "append"},
        "dlq": {"connection": "dst", "table": "bad_orders", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    run_after = await _run_one(session, "ii1")

    # Run succeeded (DLQ catches the records — doesn't fail the run).
    assert run_after.status == RunStatus.SUCCEEDED

    # Good rows landed in the clean sink.
    assert _query_rows(db_path, "SELECT id, amount FROM clean_orders ORDER BY id") == [
        (1, 100),
        (3, 200),
        (5, 300),
    ]
    # Bad rows landed in the DLQ.
    assert _query_rows(db_path, "SELECT id, amount FROM bad_orders ORDER BY id") == [
        (2, -50),
        (4, -75),
    ]


# ===== II2: DLQ asset is registered in the catalog ==========================


async def test_ii2_dlq_sink_is_a_catalog_output_asset(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The DLQ destination is just another sink as far as the catalog is
    concerned. Operators clicking through asset lineage should see both
    ``dst/clean_orders`` and ``dst/bad_orders`` listed as outputs of the
    pipeline. Without this row the question "where did my failed
    records go?" has no answer in the catalog."""
    db_path = _seed_orders_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="ii2-dlq")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, amount FROM raw_orders"},
        "transforms": [
            {
                "type": "custom_python",
                "code": (
                    "def transform(record):\n"
                    "    if record.data['amount'] < 0:\n"
                    "        raise ValueError('negative amount')\n"
                    "    return record\n"
                ),
            }
        ],
        "sink": {"connection": "dst", "table": "clean_orders", "mode": "append"},
        "dlq": {"connection": "dst", "table": "bad_orders", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await _run_one(session, "ii2")

    repo = AssetRepository(session)
    assets = {a.asset_key for a in await repo.list_for_workspace(workspace_id=ws.id)}
    assert "src/raw_orders" in assets
    assert "dst/clean_orders" in assets
    # The interesting assertion — Phase II promises the DLQ shows up too.
    assert "dst/bad_orders" in assets, (
        "DLQ sink not in catalog: operators wouldn't know where to look "
        f"for failed records. Got: {sorted(assets)}"
    )
