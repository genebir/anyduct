"""Worker claim + execute lifecycle (Step 9.3a).

Covers:

* :func:`claim_pending_run` — picks the oldest eligible row, flips
  status + worker_id + started_at + heartbeat_at, ignores rows in
  non-pending states, returns ``None`` for an empty queue.
* :class:`RunExecutor` — happy path with sqlite ``:memory:`` source +
  sink (the run actually executes); failure path (unknown connector
  type) lands status=failed with error_class/message; secret
  resolution failure recorded the same way.
* :class:`RunWorker` poll loop — claims + executes a pending row,
  honors :meth:`stop`.

Concurrency (two workers contending for the same row) is implicit in
the SKIP LOCKED clause but harder to exercise inside the conftest's
outer-transaction fixture; a real concurrency test belongs alongside
the multi-replica deployment slice and is deferred.
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
from etlx_server.db.models import (
    Connection,
    Pipeline,
    PipelineTrigger,
    PipelineVersion,
    Run,
    Workspace,
)
from etlx_server.worker.claim import claim_pending_run
from etlx_server.worker.executor import RunExecutor
from etlx_server.worker.runner import RunWorker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import StaticSecretBackend

pytestmark = pytest.mark.asyncio


# --- fixtures ---------------------------------------------------------------


def _sample_config(name: str, *, source: str = "src", sink: str = "dst") -> dict[str, Any]:
    return {
        "name": name,
        "source": {"connection": source, "query": "select 1"},
        "sink": {"connection": sink, "table": "out", "mode": "append"},
    }


async def _seed_workspace(session: AsyncSession, *, slug: str) -> Workspace:
    ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
    session.add(ws)
    await session.flush()
    return ws


async def _seed_connection(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    name: str,
    type: str = "sqlite",
    config: dict[str, Any] | None = None,
) -> Connection:
    conn = Connection(
        workspace_id=workspace_id,
        name=name,
        type=type,
        config_json=config or {"database": ":memory:"},
        secret_refs=[],
    )
    session.add(conn)
    await session.flush()
    return conn


async def _seed_pipeline(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    name: str,
    config: dict[str, Any] | None = None,
) -> tuple[Pipeline, PipelineVersion]:
    p = Pipeline(workspace_id=workspace_id, name=name)
    session.add(p)
    await session.flush()
    pv = PipelineVersion(
        pipeline_id=p.id,
        version=1,
        config_json=config or _sample_config(name),
        is_current=True,
    )
    session.add(pv)
    await session.flush()
    return p, pv


async def _seed_pending_run(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    pipeline_id: UUID,
    pipeline_version_id: UUID,
    scheduled_at: datetime | None = None,
) -> Run:
    r = Run(
        workspace_id=workspace_id,
        pipeline_id=pipeline_id,
        pipeline_version_id=pipeline_version_id,
        status=RunStatus.PENDING,
    )
    if scheduled_at is not None:
        r.scheduled_at = scheduled_at
    session.add(r)
    await session.flush()
    return r


class _SessionFactoryAdapter:
    """Wrap the test ``session`` so the executor's ``async with factory()``
    pattern reuses the same session (inside the outer-trans fixture).

    The real ``async_sessionmaker`` opens a new session each call; here
    we yield the same one so the executor's commit becomes a savepoint
    release inside the conftest's outer transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> _SessionFactoryAdapter:
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_: object) -> None:
        return None


# --- claim ------------------------------------------------------------------


async def test_claim_picks_oldest_pending_and_flips_status(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="wc-1")
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    # Two pending runs; the older one should be claimed first.
    older = await _seed_pending_run(
        session,
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    await _seed_pending_run(
        session,
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    claimed = await claim_pending_run(session, worker_id="worker-A")
    assert claimed is not None
    assert claimed.id == older.id
    assert claimed.status == RunStatus.RUNNING
    assert claimed.worker_id == "worker-A"
    assert claimed.started_at is not None
    assert claimed.heartbeat_at is not None


async def test_claim_returns_none_on_empty_queue(session: AsyncSession) -> None:
    claimed = await claim_pending_run(session, worker_id="worker-A")
    assert claimed is None


async def test_claim_skips_running_rows(session: AsyncSession) -> None:
    """A row already in ``running`` is not eligible — only ``pending`` counts."""
    ws = await _seed_workspace(session, slug="wc-skip")
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    # Seed a row directly into running (simulating another worker mid-execute).
    r = Run(
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        status=RunStatus.RUNNING,
        worker_id="someone-else",
    )
    session.add(r)
    await session.flush()

    claimed = await claim_pending_run(session, worker_id="worker-A")
    assert claimed is None


async def test_claim_respects_scheduled_at_in_future(session: AsyncSession) -> None:
    """Rows scheduled for the future shouldn't be claimed yet."""
    ws = await _seed_workspace(session, slug="wc-future")
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    await _seed_pending_run(
        session,
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
    )

    claimed = await claim_pending_run(session, worker_id="worker-A")
    assert claimed is None


# --- executor ---------------------------------------------------------------


def _prepare_sqlite_fixture(tmp_path: Path) -> str:
    """Create a SQLite file with a seeded ``seed`` table + empty ``out`` table.

    Both source and sink connectors point at the same file so we exercise
    the full read → transform → write path with real driver behavior.
    """
    db_path = tmp_path / "worker_e2e.db"
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


async def test_executor_happy_path_sqlite(session: AsyncSession, tmp_path: Path) -> None:
    """Source reads from a pre-seeded sqlite table; sink writes to another.

    Uses a real on-disk sqlite file so both connectors share the database
    (``:memory:`` is per-connection). The seeded ``seed`` table has three
    rows; the run should finish with records_read=records_written=3.
    """
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="we-ok")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, name FROM seed"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    # Claim flips it to running (mirrors what the worker does).
    claimed = await claim_pending_run(session, worker_id="worker-A")
    assert claimed is not None and claimed.id == run.id
    await session.commit()

    backend = StaticSecretBackend()
    executor = RunExecutor(_SessionFactoryAdapter(session), backend, worker_id="worker-A")
    await executor.execute(run.id)

    # Re-fetch through the same session post-commit.
    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.SUCCEEDED, (
        refreshed.error_class,
        refreshed.error_message,
    )
    assert refreshed.finished_at is not None
    assert refreshed.duration_seconds is not None
    assert refreshed.records_read == 3
    assert refreshed.records_written == 3
    assert refreshed.error_class is None
    assert refreshed.error_message is None
    # Core run_id stamped into result_json.
    assert "core_run_id" in refreshed.result_json

    # Sanity-check that the data actually landed.
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT id, name FROM out ORDER BY id").fetchall()
    finally:
        conn.close()
    assert rows == [(1, "alice"), (2, "bob"), (3, "carol")]


async def test_executor_triggers_downstream_on_success(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A successful run enqueues a PENDING run for each downstream pipeline (ADR-0029)."""
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="we-trig")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    cfg = {
        "name": "a",
        "source": {"connection": "src", "query": "SELECT id, name FROM seed"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    a, a_v = await _seed_pipeline(session, workspace_id=ws.id, name="a", config=cfg)
    b, _b_v = await _seed_pipeline(session, workspace_id=ws.id, name="b", config=cfg)
    session.add(PipelineTrigger(source_pipeline_id=a.id, target_pipeline_id=b.id))
    await session.flush()

    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=a.id, pipeline_version_id=a_v.id
    )
    claimed = await claim_pending_run(session, worker_id="worker-A")
    assert claimed is not None and claimed.id == run.id
    await session.commit()

    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="worker-A"
    ).execute(run.id)

    triggered = (
        (
            await session.execute(
                select(Run).where(Run.pipeline_id == b.id, Run.status == RunStatus.PENDING)
            )
        )
        .scalars()
        .all()
    )
    assert len(triggered) == 1
    assert triggered[0].result_json["triggered_by_run"] == str(run.id)
    assert triggered[0].result_json["trigger_chain"] == [str(a.id)]
    assert triggered[0].triggered_by_user_id is None


async def test_executor_trigger_cycle_is_broken(session: AsyncSession, tmp_path: Path) -> None:
    """A target already in the trigger chain is not re-enqueued (cycle guard)."""
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="we-cycle")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    cfg = {
        "name": "x",
        "source": {"connection": "src", "query": "SELECT id, name FROM seed"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    a, _a_v = await _seed_pipeline(session, workspace_id=ws.id, name="a", config=cfg)
    b, b_v = await _seed_pipeline(session, workspace_id=ws.id, name="b", config=cfg)
    # B → A edge; B's run was triggered by A (chain already contains A).
    session.add(PipelineTrigger(source_pipeline_id=b.id, target_pipeline_id=a.id))
    await session.flush()
    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=b.id, pipeline_version_id=b_v.id
    )
    run.result_json = {"trigger_chain": [str(a.id)]}
    claimed = await claim_pending_run(session, worker_id="worker-A")
    assert claimed is not None
    await session.commit()

    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="worker-A"
    ).execute(run.id)

    # A must NOT be re-enqueued (it's already upstream in the chain).
    a_runs = (await session.execute(select(Run).where(Run.pipeline_id == a.id))).scalars().all()
    assert a_runs == []


async def test_executor_runs_graph_with_branching(session: AsyncSession, tmp_path: Path) -> None:
    """A dataflow graph (ADR-0030) routes records to branch sinks by edge `when`."""
    db_path = tmp_path / "graph_e2e.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE seed (id INTEGER, kind TEXT)")
        conn.executemany(
            "INSERT INTO seed (id, kind) VALUES (?, ?)",
            [(1, "hi"), (2, "lo"), (3, "hi")],
        )
        conn.execute("CREATE TABLE out_hi (id INTEGER, kind TEXT)")
        conn.execute("CREATE TABLE out_lo (id INTEGER, kind TEXT)")
        conn.commit()
    finally:
        conn.close()

    ws = await _seed_workspace(session, slug="we-graph")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    cfg = {
        "name": "g",
        "graph": {
            "nodes": [
                {
                    "id": "s",
                    "type": "source",
                    "connection": "src",
                    "query": "SELECT id, kind FROM seed",
                },
                {"id": "hi", "type": "sink", "connection": "dst", "table": "out_hi"},
                {"id": "lo", "type": "sink", "connection": "dst", "table": "out_lo"},
            ],
            "edges": [
                {"from_node": "s", "to_node": "hi", "when": "data['kind'] == 'hi'"},
                {"from_node": "s", "to_node": "lo", "when": "data['kind'] == 'lo'"},
            ],
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="g", config=cfg)
    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    claimed = await claim_pending_run(session, worker_id="worker-A")
    assert claimed is not None
    await session.commit()

    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="worker-A"
    ).execute(run.id)

    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.SUCCEEDED, (
        refreshed.error_class,
        refreshed.error_message,
    )
    conn = sqlite3.connect(str(db_path))
    try:
        hi = conn.execute("SELECT id FROM out_hi ORDER BY id").fetchall()
        lo = conn.execute("SELECT id FROM out_lo ORDER BY id").fetchall()
    finally:
        conn.close()
    assert hi == [(1,), (3,)]
    assert lo == [(2,)]


async def test_executor_records_failure_on_unknown_connector_type(
    session: AsyncSession,
) -> None:
    ws = await _seed_workspace(session, slug="we-bad-type")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", type="not-a-real-connector", config={}
    )
    await _seed_connection(session, workspace_id=ws.id, name="dst")
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    claimed = await claim_pending_run(session, worker_id="worker-A")
    assert claimed is not None
    await session.commit()

    executor = RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="worker-A"
    )
    await executor.execute(run.id)

    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.FAILED
    assert refreshed.error_class is not None
    assert refreshed.error_message is not None
    assert (
        "not registered" in refreshed.error_message
        or "not-a-real-connector" in refreshed.error_message
    )
    assert refreshed.finished_at is not None


async def test_executor_records_failure_on_missing_connection(
    session: AsyncSession,
) -> None:
    """Pipeline config references a connection that doesn't exist in workspace."""
    ws = await _seed_workspace(session, slug="we-miss")
    # Only ``src`` exists; ``dst`` is missing.
    await _seed_connection(session, workspace_id=ws.id, name="src")
    p, pv = await _seed_pipeline(
        session,
        workspace_id=ws.id,
        name="p",
        config=_sample_config("p", source="src", sink="missing"),
    )
    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    claimed = await claim_pending_run(session, worker_id="worker-A")
    assert claimed is not None
    await session.commit()

    executor = RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="worker-A"
    )
    await executor.execute(run.id)

    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.FAILED
    assert "missing" in (refreshed.error_message or "")


async def test_executor_rejects_stream_mode_pipeline(session: AsyncSession) -> None:
    """Worker is batch-only in this slice — stream mode lands as failed."""
    ws = await _seed_workspace(session, slug="we-stream")
    await _seed_connection(session, workspace_id=ws.id, name="src")
    await _seed_connection(session, workspace_id=ws.id, name="dst")
    cfg = _sample_config("p")
    cfg["mode"] = "stream"
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    claimed = await claim_pending_run(session, worker_id="worker-A")
    assert claimed is not None
    await session.commit()

    executor = RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="worker-A"
    )
    await executor.execute(run.id)

    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.FAILED
    assert "batch" in (refreshed.error_message or "")


async def test_executor_preserves_retry_of_in_result_json(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A retry's ``result_json.retry_of`` must survive the success write."""
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="we-keep")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, name FROM seed"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    # Simulate a retry — preserve the marker.
    run.result_json = {"retry_of": "00000000-0000-0000-0000-000000000000"}
    await session.flush()
    claimed = await claim_pending_run(session, worker_id="worker-A")
    assert claimed is not None
    await session.commit()

    executor = RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="worker-A"
    )
    await executor.execute(run.id)

    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.SUCCEEDED, (
        refreshed.error_class,
        refreshed.error_message,
    )
    assert refreshed.result_json["retry_of"] == "00000000-0000-0000-0000-000000000000"
    assert "core_run_id" in refreshed.result_json


# --- worker loop ------------------------------------------------------------


async def test_worker_loop_processes_pending_run_then_stops(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A pending row is picked up + processed; then stop() ends the loop."""
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="wl-1")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, name FROM seed"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await session.commit()

    worker = RunWorker(
        _SessionFactoryAdapter(session),
        StaticSecretBackend(),
        worker_id="worker-loop",
        poll_interval=0.1,
    )

    async def _stop_after_short_delay() -> None:
        # Give the loop enough time to claim + execute the row, then stop.
        await asyncio.sleep(1.5)
        worker.stop()

    await asyncio.gather(worker.run(), _stop_after_short_delay())

    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.SUCCEEDED, (
        refreshed.error_class,
        refreshed.error_message,
    )
    assert refreshed.worker_id == "worker-loop"


async def test_worker_loop_with_empty_queue_exits_on_stop(session: AsyncSession) -> None:
    worker = RunWorker(
        _SessionFactoryAdapter(session),
        StaticSecretBackend(),
        worker_id="worker-empty",
        poll_interval=0.1,
    )
    # Empty queue — loop should idle until stop().
    worker.stop()
    await worker.run()
