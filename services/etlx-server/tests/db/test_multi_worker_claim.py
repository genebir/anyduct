"""Multi-replica worker clustering proof (ADR-0021 queue / ADR-0093 Phase 3).

``test_worker_lifecycle.py`` deferred "a real concurrency test" because its
outer-transaction ``session`` fixture funnels everything through ONE database
connection — but ``FOR UPDATE SKIP LOCKED`` semantics only show up across
*separate* connections. These tests therefore build their own committed
sessions straight off ``metadata_engine`` (one connection per simulated
worker replica) and clean up everything they commit.

Two layers:

* claim layer — N replicas hammering ``claim_pending_run`` concurrently
  never double-claim and together drain the queue exactly once;
* worker layer — two real :class:`RunWorker` poll loops against the same
  Postgres execute every pending run to SUCCEEDED with real sqlite I/O,
  each run stamped with the replica that owned it.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from etlx_server.db.enums import RunStatus
from etlx_server.db.models import Connection as ConnectionModel
from etlx_server.db.models import Pipeline, PipelineVersion, Run, Workspace
from etlx_server.worker.claim import claim_pending_run
from etlx_server.worker.runner import RunWorker
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from etl_plugins.config.secrets import StaticSecretBackend

pytestmark = pytest.mark.it


def _sqlite_fixture(tmp_path: Path, name: str) -> str:
    db_path = tmp_path / name
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE seed (id INTEGER, name TEXT)")
        conn.executemany(
            "INSERT INTO seed (id, name) VALUES (?, ?)",
            [(1, "alice"), (2, "bob"), (3, "carol")],
        )
        conn.execute("CREATE TABLE out (id INTEGER, name TEXT)")
        conn.commit()
    finally:
        conn.close()
    return str(db_path)


async def _seed_committed(
    factory: async_sessionmaker[AsyncSession],
    *,
    slug: str,
    n_runs: int,
    db_path: str,
) -> tuple[UUID, list[UUID]]:
    """Commit a workspace + sqlite pipeline + ``n_runs`` pending runs.

    Committed (not outer-transaction) so every simulated replica's own
    connection can see and lock the rows. Returns (workspace_id, run_ids).
    """
    cfg: dict[str, Any] = {
        "name": slug,
        "source": {"connection": "src", "query": "SELECT id, name FROM seed"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    async with factory() as session:
        ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
        session.add(ws)
        await session.flush()
        for cname in ("src", "dst"):
            session.add(
                ConnectionModel(
                    workspace_id=ws.id,
                    name=cname,
                    type="sqlite",
                    config_json={"database": db_path},
                    secret_refs=[],
                )
            )
        p = Pipeline(workspace_id=ws.id, name=slug)
        session.add(p)
        await session.flush()
        pv = PipelineVersion(pipeline_id=p.id, version=1, config_json=cfg, is_current=True)
        session.add(pv)
        await session.flush()
        run_ids: list[UUID] = []
        base = datetime.now(UTC) - timedelta(minutes=5)
        for i in range(n_runs):
            r = Run(
                workspace_id=ws.id,
                pipeline_id=p.id,
                pipeline_version_id=pv.id,
                status=RunStatus.PENDING,
            )
            r.scheduled_at = base + timedelta(seconds=i)
            session.add(r)
            await session.flush()
            run_ids.append(r.id)
        await session.commit()
        return ws.id, run_ids


async def _cleanup_workspace(factory: async_sessionmaker[AsyncSession], ws_id: UUID) -> None:
    """Remove everything the test committed (workspace cascade)."""
    async with factory() as session:
        await session.execute(delete(Workspace).where(Workspace.id == ws_id))
        await session.commit()


async def test_n_replicas_drain_queue_without_double_claim(
    metadata_engine: AsyncEngine, tmp_path: Path
) -> None:
    """Three concurrent claimers (three connections) partition 12 pending
    runs without ever handing the same row to two replicas."""
    factory = async_sessionmaker(metadata_engine, expire_on_commit=False)
    db_path = _sqlite_fixture(tmp_path, "claims.db")
    ws_id, run_ids = await _seed_committed(factory, slug="mw-claims", n_runs=12, db_path=db_path)
    try:

        async def replica(worker_id: str) -> list[UUID]:
            claimed: list[UUID] = []
            while True:
                async with factory() as session:
                    run = await claim_pending_run(session, worker_id=worker_id)
                    if run is None:
                        await session.rollback()
                        return claimed
                    claimed.append(run.id)
                    await session.commit()
                # Yield so replicas interleave like real processes would.
                await asyncio.sleep(0)

        results = await asyncio.gather(replica("w-1"), replica("w-2"), replica("w-3"))
        all_claimed = [rid for claimed in results for rid in claimed]
        # Exactly-once: no double claims, nothing left behind.
        assert len(all_claimed) == len(set(all_claimed)) == len(run_ids)
        assert set(all_claimed) == set(run_ids)
        # The work actually spread: with 12 rows and interleaved loops at
        # least two replicas must have participated.
        participants = [i for i, claimed in enumerate(results) if claimed]
        assert len(participants) >= 2, f"queue was not shared: {[len(c) for c in results]}"
        # Every claimed row is RUNNING and stamped with its owner.
        async with factory() as session:
            rows = (await session.execute(select(Run).where(Run.id.in_(run_ids)))).scalars().all()
            assert all(r.status == RunStatus.RUNNING for r in rows)
            owners = {r.worker_id for r in rows}
            assert owners <= {"w-1", "w-2", "w-3"}
            assert len(owners) >= 2
    finally:
        await _cleanup_workspace(factory, ws_id)


async def test_two_run_workers_execute_all_runs_e2e(
    metadata_engine: AsyncEngine, tmp_path: Path
) -> None:
    """Two real RunWorker poll loops (separate connections, same Postgres)
    drain 6 pending runs to SUCCEEDED — the 'just add replicas' claim."""
    factory = async_sessionmaker(metadata_engine, expire_on_commit=False)
    db_path = _sqlite_fixture(tmp_path, "workers.db")
    ws_id, run_ids = await _seed_committed(factory, slug="mw-workers", n_runs=6, db_path=db_path)
    workers = [
        RunWorker(factory, StaticSecretBackend(), worker_id=f"replica-{i}", poll_interval=0.05)
        for i in (1, 2)
    ]
    tasks = [asyncio.create_task(w.run()) for w in workers]
    try:
        deadline = asyncio.get_running_loop().time() + 60
        while True:
            async with factory() as session:
                statuses = (
                    (
                        await session.execute(
                            select(Run.status, Run.worker_id).where(Run.id.in_(run_ids))
                        )
                    )
                    .tuples()
                    .all()
                )
            if all(s in (RunStatus.SUCCEEDED, RunStatus.FAILED) for s, _ in statuses):
                break
            assert asyncio.get_running_loop().time() < deadline, f"runs stuck: {statuses}"
            await asyncio.sleep(0.2)

        assert all(s == RunStatus.SUCCEEDED for s, _ in statuses), statuses
        owners = {w for _, w in statuses}
        assert owners <= {"replica-1", "replica-2"}
        # 6 runs over two pollers at 50ms — both replicas should have won
        # at least one claim.
        assert len(owners) == 2, f"one replica did everything: {statuses}"
        # Data actually landed: 6 runs x 3 seed rows appended.
        conn = sqlite3.connect(db_path)
        try:
            (count,) = conn.execute("SELECT COUNT(*) FROM out").fetchone()
        finally:
            conn.close()
        assert count == 18
    finally:
        for w in workers:
            w.stop()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _cleanup_workspace(factory, ws_id)
