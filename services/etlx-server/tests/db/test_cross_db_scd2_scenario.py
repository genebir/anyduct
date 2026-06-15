"""Cross-DB SCD Type 2 + transform dogfooding scenario (Phase XX, 2026-05-29).

User persona: a data engineer migrating a customers table from one
warehouse to another, applying a small SCD Type 2 wrap (rename one
column, add ``effective_from`` + ``is_current``) along the way. The
pipeline uses ``auto_create_table: true`` on the sink so the
destination DDL is derived automatically.

This is the dogfood that catches the *interaction* between transforms
and ``auto_create_table``: the source schema and the final
post-transform schema diverge, so the auto-create has to anticipate
the post-transform shape rather than copy the source verbatim.
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


async def _run_one(session: AsyncSession, worker_id: str) -> Run:
    claimed = await claim_pending_run(session, worker_id=worker_id)
    assert claimed is not None
    await session.commit()
    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    ).execute(claimed.id)
    rows = await session.execute(select(Run).where(Run.id == claimed.id))
    return rows.scalar_one()


# ===== XX1: rename + add_constant + auto_create_table ======================


async def test_xx1_transform_chain_then_auto_create_sink_table(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Source has ``raw_customers(id, name, country, tier)``. The pipeline:

    1. renames ``country`` → ``region`` (a declarative ``rename``),
    2. adds a literal ``effective_from`` column,
    3. adds a literal ``is_current`` column,
    4. writes to ``customers_history`` with ``auto_create_table=True``.

    The interesting question: does ``ensure_table`` produce the
    *post-transform* schema (``id, name, region, tier, effective_from,
    is_current``) or the raw source one? If the auto-create copies the
    source verbatim, the sink table will be missing the new columns and
    the write will fail at runtime.
    """
    src_path = tmp_path / "src.db"
    dst_path = tmp_path / "dst.db"
    raw = sqlite3.connect(str(src_path))
    try:
        raw.execute("CREATE TABLE raw_customers (id INTEGER, name TEXT, country TEXT, tier TEXT)")
        raw.executemany(
            "INSERT INTO raw_customers VALUES (?, ?, ?, ?)",
            [(1, "alice", "KR", "gold"), (2, "bob", "US", "silver")],
        )
        raw.commit()
    finally:
        raw.close()
    sqlite3.connect(str(dst_path)).close()  # empty dst file

    ws = await _seed_workspace(session, slug="xx1-scd2")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(src_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(dst_path)}
    )

    cfg = {
        "name": "migrate_customers",
        "source": {
            "connection": "src",
            "query": "SELECT id, name, country, tier FROM raw_customers",
        },
        "transforms": [
            {"type": "rename", "mapping": {"country": "region"}},
            {"type": "add_constant", "column": "effective_from", "value": "2026-05-01"},
            {"type": "add_constant", "column": "is_current", "value": 1},
        ],
        "sink": {
            "connection": "dst",
            "table": "customers_history",
            "mode": "append",
            "auto_create_table": True,
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    finished = await _run_one(session, "xx1")
    # Whether or not the pipeline succeeded depends on Phase XX's
    # follow-up — at this slice we expose the *current* behaviour so a
    # subsequent commit can either accept it (failure on schema mismatch)
    # or fix it (transform-aware auto-create).
    assert finished.status == RunStatus.SUCCEEDED, (
        f"transform-aware auto_create_table not implemented yet: "
        f"{finished.error_class}: {finished.error_message}"
    )

    out = sqlite3.connect(str(dst_path))
    try:
        info = out.execute('PRAGMA table_info("customers_history")').fetchall()
        col_types = {row[1]: row[2] for row in info}
    finally:
        out.close()
    # The post-transform schema is what we want; if the auto-create
    # copied source verbatim instead, ``region`` would be missing and
    # ``country`` would be there.
    assert "region" in col_types
    assert "effective_from" in col_types
    assert "is_current" in col_types
    assert "country" not in col_types
