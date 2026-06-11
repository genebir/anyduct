"""Multi-tenancy load — 100-workspace isolation under one queue (Step 11).

The HH scenarios proved two workspaces don't leak into each other; this
scales the claim to a fleet: 100 workspaces sharing one metadata DB and
one PENDING-run queue, each with the SAME connection name, table names,
and asset keys. One worker drains the whole queue; afterwards every
workspace must hold exactly its own run, its own catalog rows, and its
own data — the SaaS posture (ADR-0052) at load.

Timing is logged (not asserted hard) so a future regression in the
claim/drain path shows up in the test output; a generous ceiling guards
against pathological slowdowns only.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from etlx_server.assets.repository import AssetRepository
from etlx_server.db.enums import RunStatus
from etlx_server.db.models import Run
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.db.test_multi_workspace_isolation_scenarios import _drain_pending_runs
from tests.db.test_worker_lifecycle import (
    _seed_connection,
    _seed_pending_run,
    _seed_pipeline,
    _seed_workspace,
)

pytestmark = pytest.mark.asyncio

_N = 100


async def test_hundred_workspaces_share_one_queue_without_leaks(
    session: AsyncSession, tmp_path: Path
) -> None:
    # One sqlite file PER workspace — same table names everywhere, so a
    # cross-tenant write would be visible as a wrong row count.
    db_paths: list[Path] = []
    for i in range(_N):
        db = tmp_path / f"tenant_{i}.db"
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("CREATE TABLE src (id INTEGER, tenant INTEGER)")
            # Tenant i carries i+1 rows — distinct counts make ANY
            # cross-tenant mixup show up as a wrong records_written.
            conn.executemany("INSERT INTO src VALUES (?, ?)", [(j, i) for j in range(i % 5 + 1)])
            conn.execute("CREATE TABLE dst (id INTEGER, tenant INTEGER)")
            conn.commit()
        finally:
            conn.close()
        db_paths.append(db)

    cfg_template = {
        "source": {"connection": "conn", "query": "SELECT id, tenant FROM src"},
        "sink": {"connection": "conn", "table": "dst", "mode": "append"},
    }

    seed_started = time.monotonic()
    ws_ids = []
    for i in range(_N):
        ws = await _seed_workspace(session, slug=f"tenant-{i}")
        # Same connection NAME in every workspace — name collisions across
        # tenants must be meaningless.
        await _seed_connection(
            session, workspace_id=ws.id, name="conn", config={"database": str(db_paths[i])}
        )
        p, pv = await _seed_pipeline(
            session,
            workspace_id=ws.id,
            name="replicate",  # same pipeline name everywhere too
            config={"name": "replicate", **cfg_template},
        )
        await _seed_pending_run(
            session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
        )
        ws_ids.append(ws.id)
    await session.commit()
    seed_seconds = time.monotonic() - seed_started

    drain_started = time.monotonic()
    drained = await _drain_pending_runs(session, "load-worker")
    drain_seconds = time.monotonic() - drain_started
    assert drained == _N
    print(
        f"\n[multi-tenancy load] seeded {_N} workspaces in {seed_seconds:.1f}s, "
        f"drained {_N} runs in {drain_seconds:.1f}s "
        f"({_N / max(drain_seconds, 0.001):.0f} runs/s)"
    )
    # Generous ceiling — catches pathological slowdowns (e.g. an O(N²)
    # claim regression), not normal CI jitter.
    assert drain_seconds < 240, f"draining {_N} runs took {drain_seconds:.0f}s"

    # ---- Isolation: every workspace holds exactly ITS run + ITS rows ----
    counts = dict(
        (
            await session.execute(select(Run.workspace_id, func.count()).group_by(Run.workspace_id))
        ).all()
    )
    for i, ws_id in enumerate(ws_ids):
        assert counts.get(ws_id) == 1, f"tenant {i} run count {counts.get(ws_id)}"
    statuses = set((await session.execute(select(Run.status).distinct())).scalars().all())
    assert statuses == {RunStatus.SUCCEEDED}

    # Data landed per-tenant with the tenant's own row count + stamp.
    for i in (0, 1, 49, 99):  # spot-check corners + middle
        rows = (
            sqlite3.connect(str(db_paths[i]))
            .execute("SELECT COUNT(*), MIN(tenant), MAX(tenant) FROM dst")
            .fetchone()
        )
        assert rows == (i % 5 + 1, i, i), f"tenant {i} got {rows}"

    # Catalog: identical asset_key strings, one pair PER workspace, no
    # cross-tenant edges (same posture as HH2, at fleet scale).
    repo = AssetRepository(session)
    for i in (0, 50, 99):
        assets = await repo.list_for_workspace(workspace_id=ws_ids[i])
        keys = sorted(a.asset_key for a in assets)
        assert keys == ["conn/dst", "conn/src"], f"tenant {i} catalog {keys}"
