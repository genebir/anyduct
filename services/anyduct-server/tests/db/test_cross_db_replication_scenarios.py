"""Cross-DB replication dogfooding scenarios (Phase VV, ADR-0066, 2026-05-29).

User persona: a data engineer who wants to copy a table from one
database into another *without* hand-writing the destination DDL. The
canonical case is replicating a postgres OLTP table into a sqlite or
mysql analytics target — the column types are different in each
dialect (BIGINT/INT/INTEGER, TIMESTAMPTZ/DATETIME/TEXT, …) and getting
them right by hand is the kind of fiddly work that drives engineers to
hack one-off scripts.

ADR-0066 introduces:

* :class:`SchemaWriter` connector capability (the dual of
  :class:`SchemaInspector`).
* :mod:`etl_plugins.core.type_mapping` — vendor type strings normalised
  through a small ``CanonicalType`` set and rendered back per dialect.
* ``SinkConfig.auto_create_table`` — when True, the pipeline reads the
  source's column schema and creates the sink table before the first
  write.

This scenario walks the engineer's workflow end-to-end:

* **VV3** — sqlite → sqlite *with the sink table missing*. The pipeline
  reads source columns via ``list_columns``, creates the sink with
  matching types (length specs translated), then writes rows. Verifies
  the type-affinity collapse (BIGINT → INTEGER) and that the sink
  ends up with the right rows.
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


# ===== VV3: sqlite → sqlite, auto-create sink table =========================


async def test_vv3_sqlite_to_sqlite_auto_create_sink_table(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Source sqlite has ``orders(id BIGINT, total DECIMAL(10,2),
    created_at TIMESTAMPTZ, payload JSONB, customer VARCHAR(64))`` —
    a deliberately postgres-flavoured set of vendor types stored in
    sqlite to exercise the translator.

    The sink sqlite *does not* have an ``orders_copy`` table; the
    pipeline declares ``auto_create_table: true`` on the sink and
    the worker creates the table from the source's schema before
    writing. The verification reads back ``PRAGMA table_info`` to
    confirm the type-affinity collapse, then counts rows.
    """
    src_db = tmp_path / "src.db"
    dst_db = tmp_path / "dst.db"
    raw = sqlite3.connect(str(src_db))
    try:
        # Deliberately vendor-mixed types — sqlite stores them as
        # declared, which we'll translate when copying.
        raw.execute(
            "CREATE TABLE orders ("
            "id BIGINT, "
            "total DECIMAL(10,2), "
            "created_at TIMESTAMPTZ, "
            "payload JSONB, "
            "customer VARCHAR(64))"
        )
        raw.executemany(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?)",
            [
                (1, 100.50, "2026-05-01T00:00:00Z", '{"a": 1}', "alice"),
                (2, 250.75, "2026-05-02T00:00:00Z", '{"b": 2}', "bob"),
            ],
        )
        raw.commit()
    finally:
        raw.close()
    # Sink db exists (the connection points at the file) but the
    # ``orders_copy`` table doesn't.
    sqlite3.connect(str(dst_db)).close()

    ws = await _seed_workspace(session, slug="vv3-replication")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(src_db)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(dst_db)}
    )

    cfg = {
        "name": "copy_orders",
        "source": {
            "connection": "src",
            "query": "SELECT id, total, created_at, payload, customer FROM orders",
        },
        "sink": {
            "connection": "dst",
            "table": "orders_copy",
            "mode": "append",
            "auto_create_table": True,
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    finished = await _run_one(session, "vv3")
    assert finished.status == RunStatus.SUCCEEDED, finished.error_message
    assert finished.records_written == 2

    # The sink table now exists — confirm both the *shape* and the data.
    out = sqlite3.connect(str(dst_db))
    try:
        cols_info = out.execute('PRAGMA table_info("orders_copy")').fetchall()
        col_types = {row[1]: row[2] for row in cols_info}
        # BIGINT → sqlite type-affinity INTEGER, NUMERIC keeps precision,
        # TIMESTAMPTZ/JSONB collapse to TEXT, VARCHAR also TEXT.
        assert col_types == {
            "id": "INTEGER",
            "total": "NUMERIC(10,2)",
            "created_at": "TEXT",
            "payload": "TEXT",
            "customer": "TEXT",
        }
        rows = sorted(out.execute("SELECT id, total, customer FROM orders_copy").fetchall())
    finally:
        out.close()
    assert rows == [(1, 100.50, "alice"), (2, 250.75, "bob")]
