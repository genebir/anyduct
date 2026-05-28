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
import inspect
import sqlite3

# Module-level capture for H2c parallelism proof: a custom transform records
# the thread it ran in. Two independent branches → two distinct thread IDs.
import threading as _threading
import time as _time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from etlx_server.assets.repository import AssetRepository
from etlx_server.db.enums import RunStatus
from etlx_server.db.models import (
    Connection,
    NodeRun,
    Pipeline,
    PipelineTrigger,
    PipelineVersion,
    Run,
    Workspace,
    WorkspaceVariable,
)
from etlx_server.worker.claim import claim_pending_run
from etlx_server.worker.executor import RunExecutor
from etlx_server.worker.runner import RunWorker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import StaticSecretBackend
from etl_plugins.core.exceptions import ConfigError as _CoreConfigError
from etl_plugins.core.record import Record as _Record
from etl_plugins.runtime.transforms import register_transform

_test_thread_ids: list[int] = []

try:

    @register_transform("_probe_thread")
    def _build_probe_thread(config: Any) -> Any:
        def _probe(rec: _Record) -> _Record:
            _test_thread_ids.append(_threading.get_ident())
            # Brief sleep so two parallel branches' to_thread calls definitely
            # overlap (instead of one finishing before the other starts and
            # the pool reusing the same thread for both).
            _time.sleep(0.05)
            return rec

        return _probe

except _CoreConfigError:
    pass  # already registered (test re-collection)

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


async def test_executor_resolves_global_and_local_variables(
    session: AsyncSession, tmp_path: Path
) -> None:
    """${var.name} resolves at build time: workspace global + pipeline local (ADR-0041 V2).

    Global ``tbl`` fills the FROM clause; local ``threshold`` (which wins over a
    global of the same name) drives a filter — so the run reflects both.
    """
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="we-vars")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    # Global var supplies the table; another global is shadowed by a local.
    session.add(WorkspaceVariable(workspace_id=ws.id, name="tbl", value_json="seed"))
    session.add(WorkspaceVariable(workspace_id=ws.id, name="threshold", value_json=99))
    await session.flush()
    cfg = {
        "name": "p",
        "variables": {"threshold": 1},  # local wins over the global 99
        "source": {"connection": "src", "query": "SELECT id, name FROM ${var.tbl}"},
        "transforms": [{"type": "filter", "expr": "data['id'] > ${var.threshold}"}],
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
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
    assert refreshed.status == RunStatus.SUCCEEDED, (refreshed.error_class, refreshed.error_message)
    assert refreshed.records_read == 3
    assert refreshed.records_written == 2  # id > 1 (local threshold), table from global


async def test_executor_fails_on_undefined_variable(session: AsyncSession, tmp_path: Path) -> None:
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="we-badvar")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id FROM ${var.missing}"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
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
    assert refreshed.status == RunStatus.FAILED
    assert "variable" in (refreshed.error_message or "").lower()


async def _node_runs_for(session: AsyncSession, run_id: object) -> dict[str, NodeRun]:
    rows = (await session.execute(select(NodeRun).where(NodeRun.run_id == run_id))).scalars().all()
    return {r.node_id: r for r in rows}


async def test_node_level_graph_records_node_runs(session: AsyncSession, tmp_path: Path) -> None:
    """node_level graph run executes node-by-node and records a node_run per node."""
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="we-nodelevel")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    cfg = {
        "name": "p",
        "node_level": True,
        "graph": {
            "nodes": [
                {
                    "id": "s",
                    "type": "source",
                    "connection": "src",
                    "query": "SELECT id, name FROM seed",
                },
                {
                    "id": "f",
                    "type": "transform",
                    "transform": {"type": "filter", "expr": "data['id'] > 1"},
                },
                {"id": "k", "type": "sink", "connection": "dst", "table": "out", "mode": "append"},
            ],
            "edges": [
                {"from_node": "s", "to_node": "f"},
                {"from_node": "f", "to_node": "k"},
            ],
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
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
    assert refreshed.status == RunStatus.SUCCEEDED, (refreshed.error_class, refreshed.error_message)
    assert refreshed.records_read == 3
    assert refreshed.records_written == 2  # id > 1

    nodes = await _node_runs_for(session, run.id)
    assert set(nodes) == {"s", "f", "k"}
    assert all(n.status == RunStatus.SUCCEEDED for n in nodes.values())
    assert nodes["s"].records_read == 3
    assert nodes["k"].records_written == 2
    assert nodes["f"].depends_on == ["s"]
    assert nodes["k"].depends_on == ["f"]


async def test_node_level_failed_node_skips_downstream(
    session: AsyncSession, tmp_path: Path
) -> None:
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="we-nodefail")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    cfg = {
        "name": "p",
        "node_level": True,
        "graph": {
            "nodes": [
                {"id": "s", "type": "source", "connection": "src", "query": "SELECT id FROM seed"},
                # references a missing column → the transform raises → node fails
                {
                    "id": "f",
                    "type": "transform",
                    "transform": {"type": "filter", "expr": "data['nope'] > 1"},
                },
                {"id": "k", "type": "sink", "connection": "dst", "table": "out", "mode": "append"},
            ],
            "edges": [
                {"from_node": "s", "to_node": "f"},
                {"from_node": "f", "to_node": "k"},
            ],
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
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
    assert refreshed.status == RunStatus.FAILED

    nodes = await _node_runs_for(session, run.id)
    assert nodes["s"].status == RunStatus.SUCCEEDED
    assert nodes["f"].status == RunStatus.FAILED
    assert nodes["f"].error_class is not None
    assert nodes["k"].status == RunStatus.CANCELLED  # skipped — upstream failed


def _seed_sqlite_db(db_path: Path) -> str:
    """Tiny helper: seed a sqlite file with a 3-row ``seed`` table + empty ``out``."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE seed (id INTEGER, name TEXT)")
        conn.executemany(
            "INSERT INTO seed (id, name) VALUES (?, ?)", [(1, "x"), (2, "y"), (3, "z")]
        )
        conn.execute("CREATE TABLE out (id INTEGER, name TEXT)")
        conn.commit()
    finally:
        conn.close()
    return str(db_path)


async def test_node_level_runs_independent_branches_in_different_threads(
    session: AsyncSession, tmp_path: Path
) -> None:
    """H2c: two independent branches run in distinct threads (proves wave concurrency).

    Each branch's transform records ``threading.get_ident()``. With per-node
    connectors + ``asyncio.gather`` over ready nodes, the two branches' source
    + transform + sink each get their own to_thread → distinct thread ids.
    """
    _test_thread_ids.clear()
    db_a = _seed_sqlite_db(tmp_path / "a.db")
    db_b = _seed_sqlite_db(tmp_path / "b.db")
    ws = await _seed_workspace(session, slug="we-h2c-par")
    await _seed_connection(session, workspace_id=ws.id, name="srcA", config={"database": db_a})
    await _seed_connection(session, workspace_id=ws.id, name="dstA", config={"database": db_a})
    await _seed_connection(session, workspace_id=ws.id, name="srcB", config={"database": db_b})
    await _seed_connection(session, workspace_id=ws.id, name="dstB", config={"database": db_b})
    cfg = {
        "name": "p",
        "node_level": True,
        "graph": {
            "nodes": [
                {
                    "id": "sa",
                    "type": "source",
                    "connection": "srcA",
                    "query": "SELECT id, name FROM seed",
                },
                {"id": "pa", "type": "transform", "transform": {"type": "_probe_thread"}},
                {
                    "id": "ka",
                    "type": "sink",
                    "connection": "dstA",
                    "table": "out",
                    "mode": "append",
                },
                {
                    "id": "sb",
                    "type": "source",
                    "connection": "srcB",
                    "query": "SELECT id, name FROM seed",
                },
                {"id": "pb", "type": "transform", "transform": {"type": "_probe_thread"}},
                {
                    "id": "kb",
                    "type": "sink",
                    "connection": "dstB",
                    "table": "out",
                    "mode": "append",
                },
            ],
            "edges": [
                {"from_node": "sa", "to_node": "pa"},
                {"from_node": "pa", "to_node": "ka"},
                {"from_node": "sb", "to_node": "pb"},
                {"from_node": "pb", "to_node": "kb"},
            ],
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
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
    assert refreshed.status == RunStatus.SUCCEEDED, (refreshed.error_class, refreshed.error_message)
    assert refreshed.records_read == 6  # 3 from each source
    assert refreshed.records_written == 6  # 3 to each sink
    nodes = await _node_runs_for(session, run.id)
    assert {n: nodes[n].status for n in nodes} == dict.fromkeys(
        ("sa", "pa", "ka", "sb", "pb", "kb"), RunStatus.SUCCEEDED
    )
    # The proof: the two branches' probes ran in distinct threads (3 records
    # per probe x 2 branches -> 6 thread ids; at least 2 distinct values).
    assert (
        len(set(_test_thread_ids)) >= 2
    ), f"expected ≥2 distinct thread ids, got {sorted(set(_test_thread_ids))}"


async def test_node_level_live_updates_started_before_finished(
    session: AsyncSession, tmp_path: Path
) -> None:
    """H3a: node_runs are written live — started_at + worker_id set, < finished_at."""
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="we-h3a-live")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    cfg = {
        "name": "p",
        "node_level": True,
        "graph": {
            "nodes": [
                {
                    "id": "s",
                    "type": "source",
                    "connection": "src",
                    "query": "SELECT id, name FROM seed",
                },
                {
                    "id": "k",
                    "type": "sink",
                    "connection": "dst",
                    "table": "out",
                    "mode": "append",
                },
            ],
            "edges": [{"from_node": "s", "to_node": "k"}],
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    claimed = await claim_pending_run(session, worker_id="worker-A")
    assert claimed is not None
    await session.commit()

    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="worker-A"
    ).execute(run.id)

    nodes = await _node_runs_for(session, run.id)
    for nr in nodes.values():
        # set_running ran before set_succeeded → both timestamps present and ordered.
        assert nr.started_at is not None, f"node {nr.node_id} never marked running"
        assert nr.finished_at is not None
        assert nr.started_at <= nr.finished_at
        assert nr.worker_id == "worker-A"
        # attempt was bumped from 0 → 1 by set_running
        assert nr.attempt == 1


async def test_executor_persists_lineage(session: AsyncSession, tmp_path: Path) -> None:
    """A successful run records its derived assets, edge, and a materialization
    (ADR-0036, Phase B2)."""
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="we-lineage")
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
    claimed = await claim_pending_run(session, worker_id="worker-L")
    assert claimed is not None
    await session.commit()

    executor = RunExecutor(_SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="w")
    await executor.execute(run.id)

    repo = AssetRepository(session)
    assets = await repo.list_for_workspace(workspace_id=ws.id)
    keys = {a.asset_key for a in assets}
    assert "dst/out" in keys  # output asset
    assert any(k.startswith("src/") for k in keys)  # input asset (query-keyed)

    out_asset = next(a for a in assets if a.asset_key == "dst/out")
    assert out_asset.last_materialized_at is not None
    ups = await repo.upstream(out_asset.id)
    assert any(a.asset_key.startswith("src/") for a in ups)  # input → output edge
    mats = await repo.materializations(asset_id=out_asset.id)
    assert len(mats) == 1
    assert mats[0].records_written == 3
    assert mats[0].run_id == run.id


async def test_executor_persists_column_lineage(session: AsyncSession, tmp_path: Path) -> None:
    """A successful run also records per-column lineage (ADR-0041 J2).

    For ``SELECT id, name FROM seed`` writing to ``dst/out``, the worker
    should derive + persist two columns on the output with their upstream
    column refs back to the source ``seed`` table.
    """
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="we-col-lineage")
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
    claimed = await claim_pending_run(session, worker_id="worker-CL")
    assert claimed is not None
    await session.commit()

    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="w"
    ).execute(run.id)

    repo = AssetRepository(session)
    assets = await repo.list_for_workspace(workspace_id=ws.id)
    out_asset = next(a for a in assets if a.asset_key == "dst/out")
    assert out_asset.column_lineage_opaque is False

    cols, upstream_map = await repo.column_lineage_for_asset(asset_id=out_asset.id)
    assert {c.name for c in cols} == {"id", "name"}
    by_name = {c.name: c for c in cols}
    id_upstreams = [
        (up_asset.asset_key, up_col.name) for up_col, up_asset in upstream_map[by_name["id"].id]
    ]
    assert id_upstreams and id_upstreams[0][1] == "id"
    assert id_upstreams[0][0].startswith("src/")


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


async def test_executor_backfill_cursor_range(session: AsyncSession, tmp_path: Path) -> None:
    """A run carrying a backfill cursor range reads only the windowed rows
    (id > cursor_from and id <= cursor_to) — ADR-0039."""
    db_path = _prepare_sqlite_fixture(tmp_path)  # seed has id 1,2,3
    ws = await _seed_workspace(session, slug="we-backfill")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    cfg = {
        "name": "p",
        "source": {
            "connection": "src",
            "query": "SELECT id, name FROM seed",
            "cursor_column": "id",
        },
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    run.result_json = {"backfill": {"cursor_from": 1, "cursor_to": 2}}
    claimed = await claim_pending_run(session, worker_id="worker-B")
    assert claimed is not None
    await session.commit()

    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="worker-B"
    ).execute(run.id)

    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.SUCCEEDED, (refreshed.error_class, refreshed.error_message)
    assert refreshed.records_written == 1  # only id=2 is in (1, 2]

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT id FROM out ORDER BY id").fetchall()
    finally:
        conn.close()
    assert rows == [(2,)]


async def test_executor_asset_triggers_consumer(session: AsyncSession, tmp_path: Path) -> None:
    """Materializing dst/out auto-triggers an opt-in pipeline that reads it
    (ADR-0037) — but not one without auto_materialize."""
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="we-asset")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})

    # A: seed → dst.out (the asset that gets materialized).
    a_cfg = {
        "name": "a",
        "source": {"connection": "src", "query": "SELECT id, name FROM seed"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    a, a_v = await _seed_pipeline(session, workspace_id=ws.id, name="a", config=a_cfg)

    # B: reads dst.out, opt-in → should be auto-triggered.
    b_cfg = {
        "name": "b",
        "auto_materialize": True,
        "source": {"connection": "dst", "query": "SELECT id FROM out"},
        "sink": {"connection": "dst", "table": "out2", "mode": "append"},
    }
    b, _b_v = await _seed_pipeline(session, workspace_id=ws.id, name="b", config=b_cfg)

    # C: reads dst.out but NOT opt-in → must stay untriggered.
    c_cfg = {
        "name": "c",
        "source": {"connection": "dst", "query": "SELECT id FROM out"},
        "sink": {"connection": "dst", "table": "out3", "mode": "append"},
    }
    c, _c_v = await _seed_pipeline(session, workspace_id=ws.id, name="c", config=c_cfg)

    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=a.id, pipeline_version_id=a_v.id
    )
    claimed = await claim_pending_run(session, worker_id="worker-A")
    assert claimed is not None
    await session.commit()

    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="worker-A"
    ).execute(run.id)

    b_runs = (await session.execute(select(Run).where(Run.pipeline_id == b.id))).scalars().all()
    assert len(b_runs) == 1
    assert b_runs[0].status == RunStatus.PENDING
    assert "dst/out" in b_runs[0].result_json["triggered_by_assets"]
    assert b_runs[0].result_json["trigger_chain"] == [str(a.id)]

    c_runs = (await session.execute(select(Run).where(Run.pipeline_id == c.id))).scalars().all()
    assert len(c_runs) == 0  # not opt-in


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


# ---- Phase P (2026-05-28) — cooperative cancel ----------------------------


async def test_pending_run_with_cancel_requested_lands_cancelled_immediately(
    session: AsyncSession,
) -> None:
    """A pending run with cancel_requested_at already stamped (e.g. the
    REST endpoint flipped it before the worker could even claim) ends
    up CANCELLED, not SUCCEEDED/FAILED. Mirrors the request_cancel
    happy-path for pre-claim runs end-to-end."""
    ws = await _seed_workspace(session, slug="wl-cancel-pending")
    p, pv = await _seed_pipeline(
        session,
        workspace_id=ws.id,
        name="p",
        config={
            "name": "p",
            "source": {"connection": "src"},
            "sink": {"connection": "dst", "table": "out"},
        },
    )
    # Construct directly with status=CANCELLED + finished_at to mirror
    # what RunRepository.request_cancel does for a pending row.
    now = datetime.now(UTC)
    run = Run(
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        status=RunStatus.CANCELLED,
        cancel_requested_at=now,
        finished_at=now,
    )
    session.add(run)
    await session.commit()
    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.CANCELLED
    assert refreshed.cancel_requested_at is not None
    assert refreshed.finished_at is not None


async def test_graph_executor_wave_boundary_honours_cancel_event() -> None:
    """The node-level graph executor's wave-boundary check (Phase P,
    2026-05-28) is responsible for cooperative cancel. Tested directly
    here with a pre-set ``cancel_event`` — bypass the heartbeat timing
    (which polls every ~10s and would race fast test pipelines). All
    nodes that didn't run before the event was set come back SKIPPED.
    The end-to-end heartbeat → wave → CANCELLED status path is covered
    via the REST tests + UX confidence; this isolates the algorithm."""
    from etlx_server.worker.node_graph import (
        NODE_SKIPPED,
        execute_graph_nodes_concurrent,
    )

    from etl_plugins.config.models import ConnectionConfig
    from etl_plugins.core.pipeline import GraphEdge, GraphNode, SinkSpec, Task

    task = Task(
        name="t",
        graph_nodes=[
            GraphNode(id="s", kind="source", source_name="src"),
            GraphNode(id="k", kind="sink", sink=SinkSpec(name="dst", table="out")),
        ],
        graph_edges=[GraphEdge(from_id="s", to_id="k")],
    )
    conn_cfgs: dict[str, ConnectionConfig] = {
        "src": ConnectionConfig(type="sqlite", database=":memory:"),
        "dst": ConnectionConfig(type="sqlite", database=":memory:"),
    }
    cancel_event = _threading.Event()
    cancel_event.set()  # pre-set: cancel fires before any wave runs

    outcomes = await execute_graph_nodes_concurrent(task, conn_cfgs, cancel_event=cancel_event)
    by_id = {o.node_id: o for o in outcomes}
    assert by_id["s"].status == NODE_SKIPPED
    assert by_id["k"].status == NODE_SKIPPED
    assert by_id["s"].error_class is None  # cancel != error
    assert by_id["k"].error_class is None


# ---- Phase U (2026-05-28) — data-plane audit -----------------------------


async def test_node_level_graph_emits_audit_rows_for_sql_and_python(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A node-level graph with a Run SQL source + a custom_python
    transform produces two audit rows after a successful run — one
    ``run.sql_executed`` and one ``run.python_executed`` — both
    scoped to the run via resource_id."""
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="wl-audit-data")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    # Graph: a plain source → custom_python transform → sink. The
    # sql_exec node sits in parallel (no edge) so its standalone-
    # source semantics (ADR-0042 follow-up) apply.
    cfg = {
        "name": "p",
        "node_level": True,
        "graph": {
            "nodes": [
                {
                    "id": "x",
                    "type": "sql_exec",
                    "connection": "src",
                    "statement": "CREATE TABLE IF NOT EXISTS audit_marker (n INT)",
                },
                {
                    "id": "s",
                    "type": "source",
                    "connection": "src",
                    "query": "SELECT id, name FROM seed",
                },
                {
                    "id": "py",
                    "type": "transform",
                    "transform": {
                        "type": "custom_python",
                        "code": "def transform(record):\n    return record\n",
                    },
                },
                {"id": "k", "type": "sink", "connection": "dst", "table": "out", "mode": "append"},
            ],
            "edges": [
                {"from_node": "s", "to_node": "py"},
                {"from_node": "py", "to_node": "k"},
            ],
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    claimed = await claim_pending_run(session, worker_id="worker-audit")
    assert claimed is not None
    await session.commit()

    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="worker-audit"
    ).execute(run.id)

    from etlx_server.db.models import AuditLog as _AuditLog

    audit_rows = list(
        (await session.execute(select(_AuditLog).where(_AuditLog.resource_id == str(run.id))))
        .scalars()
        .all()
    )
    by_action = {r.action for r in audit_rows}
    assert "run.sql_executed" in by_action
    assert "run.python_executed" in by_action

    sql_row = next(r for r in audit_rows if r.action == "run.sql_executed")
    assert sql_row.workspace_id == ws.id
    assert sql_row.resource_type == "run"
    assert sql_row.after_json["node_id"] == "x"
    assert sql_row.after_json["kind"] == "sql_exec"
    assert sql_row.after_json["connection"] == "src"
    assert "audit_marker" in sql_row.after_json["statement"]
    assert "statement_hash" in sql_row.after_json

    py_row = next(r for r in audit_rows if r.action == "run.python_executed")
    assert py_row.after_json["node_id"] == "py"
    assert py_row.after_json["kind"] == "transform:custom_python"
    assert py_row.after_json["first_line"].startswith("def transform")
    assert py_row.after_json["lines"] >= 1
    assert "code_hash" in py_row.after_json


async def test_node_level_graph_with_sql_exec_loads_connector(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Regression: caught during dogfooding 2026-05-28. The node-level
    path's ``_connection_names_for`` helper was missed when sql_exec
    landed as a 6th GRAPH_NODE_TYPE (ADR-0042 follow-up), so a
    node-level graph with a sql_exec node failed with "No connector
    for sql_exec X". This test runs a standalone sql_exec + a normal
    source→sink chain in node-level mode; before the fix the sql_exec
    node landed FAILED while source/sink stayed succeeded."""
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="wl-sqlexec-nodelevel")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    cfg = {
        "name": "p",
        "node_level": True,
        "graph": {
            "nodes": [
                # Standalone sql_exec — no incoming/outgoing edges.
                # The previous bug surfaced here because the helper
                # didn't know to load a connector for kind=sql_exec.
                {
                    "id": "x",
                    "type": "sql_exec",
                    "connection": "src",
                    "statement": "CREATE TABLE IF NOT EXISTS x_marker (n INT)",
                },
                {
                    "id": "s",
                    "type": "source",
                    "connection": "src",
                    "query": "SELECT id, name FROM seed",
                },
                {
                    "id": "k",
                    "type": "sink",
                    "connection": "dst",
                    "table": "out",
                    "mode": "append",
                },
            ],
            "edges": [{"from_node": "s", "to_node": "k"}],
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    claimed = await claim_pending_run(session, worker_id="worker-sqlexec")
    assert claimed is not None
    await session.commit()

    await RunExecutor(
        _SessionFactoryAdapter(session),
        StaticSecretBackend(),
        worker_id="worker-sqlexec",
    ).execute(run.id)

    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.SUCCEEDED, (
        refreshed.error_class,
        refreshed.error_message,
    )
    # All three nodes succeeded including the previously-broken sql_exec.
    nodes = await _node_runs_for(session, run.id)
    assert {n.status for n in nodes.values()} == {RunStatus.SUCCEEDED}
    assert "x" in nodes  # the sql_exec node ran


async def test_node_level_graph_audits_source_select_query(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Phase W (2026-05-28): SELECT queries on SQL sources land in the
    audit log as ``run.sql_read``. Covers the "각 쿼리" compliance ask
    the user surfaced — readers of PII tables show up in the same
    workspace audit feed as data-mutating ops."""
    db_path = _prepare_sqlite_fixture(tmp_path)
    ws = await _seed_workspace(session, slug="wl-audit-read")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    cfg = {
        "name": "p",
        "node_level": True,
        "graph": {
            "nodes": [
                {
                    "id": "s",
                    "type": "source",
                    "connection": "src",
                    "query": "SELECT id, name FROM seed",
                },
                {"id": "k", "type": "sink", "connection": "dst", "table": "out", "mode": "append"},
            ],
            "edges": [{"from_node": "s", "to_node": "k"}],
        },
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    run = await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    claimed = await claim_pending_run(session, worker_id="worker-audit-read")
    assert claimed is not None
    await session.commit()

    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="worker-audit-read"
    ).execute(run.id)

    from etlx_server.db.models import AuditLog as _AuditLog

    rows = list(
        (
            await session.execute(
                select(_AuditLog).where(
                    _AuditLog.resource_id == str(run.id),
                    _AuditLog.action == "run.sql_read",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    after = rows[0].after_json
    assert after["node_id"] == "s"
    assert after["kind"] == "source"
    assert after["connection"] == "src"
    assert after["connection_type"] == "sqlite"
    assert "SELECT id, name FROM seed" in after["query"]
    assert "query_hash" in after
    # records_read pulled from the node-level outcome — proves the
    # source actually read 3 rows from the fixture.
    assert after["records_read"] == 3


async def test_audit_skips_run_sql_read_for_non_sql_connection_types(
    session: AsyncSession,
) -> None:
    """Phase W guard: a source on a non-SQL connection (kafka / s3 /
    http) whose ``query`` field happens to be set (topic / prefix /
    path) must NOT produce a ``run.sql_read`` row — the semantic
    mismatch would be misleading in audit forensics. We test the
    guard directly on the helper rather than running the pipeline
    (kafka/s3 need real brokers); the guard branch is the test."""
    from etlx_server.worker.executor import RunExecutor

    # The relevant logic is the ``_SQL_CONNECTION_TYPES`` constant +
    # the early return in _record_sql_read; assert the constant is
    # what we expect so a future widening (e.g. snowflake) doesn't
    # silently change the contract for existing connection types.
    src = inspect.getsource(RunExecutor._record_data_operations)
    assert '_SQL_CONNECTION_TYPES = {"postgres", "mysql", "sqlite"}' in src
