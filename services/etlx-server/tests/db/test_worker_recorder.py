"""Recorder bridges core observability → metadata DB (Step 9.3c).

Covers:

* :class:`RunRecorder` captures structlog events bound with ``run_id``
  into ``run_logs`` for the duration of the executor's lifetime.
* :class:`RecordingMetrics` captures every counter / histogram point
  the core emits during ``Pipeline.run`` into ``run_metrics``.
* Metric and log captures are scoped to the active run — a second
  recorder (or none) doesn't see another run's events.
* The recorder cleans up after itself: after ``__aexit__``, the metrics
  backend is restored and a stale structlog event with the same
  ``run_id`` is dropped (not written to a stranded queue).
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import structlog
from etlx_server.db.enums import LogLevel, RunStatus
from etlx_server.db.models import (
    Connection,
    Pipeline,
    PipelineVersion,
    Run,
    RunLog,
    RunMetric,
    Workspace,
)
from etlx_server.worker.claim import claim_pending_run
from etlx_server.worker.executor import RunExecutor
from etlx_server.worker.recorder import (
    RunRecorder,
    current_run_id,
    log_processor,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from etl_plugins.config.secrets import StaticSecretBackend
from etl_plugins.observability.logging import configure_logging
from etl_plugins.observability.metrics import (
    DURATION_SECONDS,
    RECORDS_READ_TOTAL,
    RECORDS_WRITTEN_TOTAL,
    get_metrics,
    reset_metrics,
)

pytestmark = pytest.mark.asyncio


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _install_log_bridge() -> Any:
    """Configure structlog with the recorder's processor for the duration of
    each test. ``configure_logging`` is idempotent; we restore a baseline
    config afterwards so other test files aren't affected."""
    configure_logging(level="DEBUG", json=True, extra_processors=[log_processor])
    yield
    # Reset structlog to defaults so subsequent tests in this file (and
    # neighbouring suites) see no surprise bridge installed.
    structlog.reset_defaults()
    reset_metrics()


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
    config: dict[str, Any],
) -> Connection:
    c = Connection(
        workspace_id=workspace_id,
        name=name,
        type="sqlite",
        config_json=config,
        secret_refs=[],
    )
    session.add(c)
    await session.flush()
    return c


async def _seed_pipeline(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    config: dict[str, Any],
) -> tuple[Pipeline, PipelineVersion]:
    p = Pipeline(workspace_id=workspace_id, name=config["name"])
    session.add(p)
    await session.flush()
    pv = PipelineVersion(pipeline_id=p.id, version=1, config_json=config, is_current=True)
    session.add(pv)
    await session.flush()
    return p, pv


async def _seed_run(
    session: AsyncSession, *, workspace_id: UUID, p: Pipeline, pv: PipelineVersion
) -> Run:
    r = Run(
        workspace_id=workspace_id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        status=RunStatus.PENDING,
    )
    session.add(r)
    await session.flush()
    return r


def _prepare_sqlite(tmp_path: Path, *, rows: int = 3) -> str:
    db_path = tmp_path / "rec.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE seed (id INTEGER, name TEXT)")
        conn.executemany(
            "INSERT INTO seed (id, name) VALUES (?, ?)",
            [(i, f"row-{i}") for i in range(1, rows + 1)],
        )
        conn.execute("CREATE TABLE out (id INTEGER, name TEXT)")
        conn.commit()
    finally:
        conn.close()
    return str(db_path)


class _SessionFactoryAdapter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> _SessionFactoryAdapter:
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_: object) -> None:
        return None


# --- recorder unit-style (no executor) --------------------------------------


async def test_recorder_writes_log_when_structlog_event_has_matching_run_id(
    session: AsyncSession,
) -> None:
    ws = await _seed_workspace(session, slug="rec-log")
    # A bare Run row is enough for FK purposes.
    p = Pipeline(workspace_id=ws.id, name="p")
    session.add(p)
    await session.flush()
    pv = PipelineVersion(pipeline_id=p.id, version=1, config_json={"name": "p"}, is_current=True)
    session.add(pv)
    await session.flush()
    run = Run(
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        status=RunStatus.RUNNING,
    )
    session.add(run)
    await session.flush()
    await session.commit()

    factory = _SessionFactoryAdapter(session)
    async with RunRecorder(factory, run.id) as recorder:  # noqa: F841
        log = structlog.get_logger().bind(run_id=str(run.id))
        log.info("hello", k="v")
        log.warning("watch out", reason="x")
        # An event whose run_id doesn't match should not be captured.
        structlog.get_logger().bind(run_id=str(UUID(int=0))).info("other-run")

    logs = (await session.execute(select(RunLog).where(RunLog.run_id == run.id))).scalars().all()
    messages = sorted(log.message for log in logs)
    assert "hello" in messages
    assert "watch out" in messages
    # The non-matching event must not show up.
    assert "other-run" not in messages


async def test_recorder_writes_metrics_for_active_run(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="rec-met")
    p = Pipeline(workspace_id=ws.id, name="p")
    session.add(p)
    await session.flush()
    pv = PipelineVersion(pipeline_id=p.id, version=1, config_json={"name": "p"}, is_current=True)
    session.add(pv)
    await session.flush()
    run = Run(
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        status=RunStatus.RUNNING,
    )
    session.add(run)
    await session.flush()
    await session.commit()

    factory = _SessionFactoryAdapter(session)
    async with RunRecorder(factory, run.id):
        token = current_run_id.set(run.id)
        try:
            metrics = get_metrics()
            metrics.counter("my.counter").add(7, {"shard": "a"})
            metrics.histogram("my.hist").record(0.42, {"phase": "warm"})
        finally:
            current_run_id.reset(token)

    rows = (
        (await session.execute(select(RunMetric).where(RunMetric.run_id == run.id))).scalars().all()
    )
    by_name = {r.name: r for r in rows}
    assert by_name["my.counter"].value == pytest.approx(7.0)
    assert by_name["my.counter"].attrs_json == {"shard": "a"}
    assert by_name["my.hist"].value == pytest.approx(0.42)
    assert by_name["my.hist"].attrs_json == {"phase": "warm"}


async def test_recorder_captures_node_id_from_contextvars(
    session: AsyncSession,
) -> None:
    """Phase M (2026-05-26): when ``node_id`` is bound via
    structlog.contextvars (the worker does this around each node's
    execution), the recorder pulls it out of the event_dict and
    persists it as a first-class column — not buried inside
    ``context_json``."""
    from structlog.contextvars import bind_contextvars, unbind_contextvars

    ws = await _seed_workspace(session, slug="rec-node")
    p = Pipeline(workspace_id=ws.id, name="p")
    session.add(p)
    await session.flush()
    pv = PipelineVersion(pipeline_id=p.id, version=1, config_json={"name": "p"}, is_current=True)
    session.add(pv)
    await session.flush()
    run = Run(
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        status=RunStatus.RUNNING,
    )
    session.add(run)
    await session.flush()
    await session.commit()

    factory = _SessionFactoryAdapter(session)
    async with RunRecorder(factory, run.id):
        log = structlog.get_logger().bind(run_id=str(run.id))
        # Outside any bind_contextvars — run-level log.
        log.info("run-level event", phase="setup")
        # Inside the bind — emulates what the worker does around each
        # node in node_graph._run_node_in_thread.
        bind_contextvars(node_id="node-A")
        try:
            log.info("hi from node A", phase="reading")
        finally:
            unbind_contextvars("node_id")
        # Back to run-level after the unbind.
        log.info("post-node summary", phase="teardown")

    logs = list(
        (await session.execute(select(RunLog).where(RunLog.run_id == run.id).order_by(RunLog.ts)))
        .scalars()
        .all()
    )
    by_msg = {row.message: row for row in logs}
    assert by_msg["run-level event"].node_id is None
    assert by_msg["hi from node A"].node_id == "node-A"
    assert by_msg["post-node summary"].node_id is None
    # node_id stripped from context_json (lives in its own column now).
    assert "node_id" not in by_msg["hi from node A"].context_json


async def test_repository_list_logs_filters_by_node(session: AsyncSession) -> None:
    """``RunRepository.list_logs(node_id=...)`` returns only matching
    rows — used by the run-detail UI when the operator clicks a node
    card. ``__run__`` returns only run-level (NULL) rows so the user
    can audit build / connector setup / summary logs separately."""
    from etlx_server.runs.repository import RunRepository

    ws = await _seed_workspace(session, slug="rec-flt")
    p = Pipeline(workspace_id=ws.id, name="p")
    session.add(p)
    await session.flush()
    pv = PipelineVersion(pipeline_id=p.id, version=1, config_json={"name": "p"}, is_current=True)
    session.add(pv)
    await session.flush()
    run = Run(
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        status=RunStatus.RUNNING,
    )
    session.add(run)
    await session.flush()

    # Seed three logs: two on different nodes + one run-level.
    session.add_all(
        [
            RunLog(run_id=run.id, level=LogLevel.INFO, message="a1", node_id="A", context_json={}),
            RunLog(run_id=run.id, level=LogLevel.INFO, message="b1", node_id="B", context_json={}),
            RunLog(run_id=run.id, level=LogLevel.INFO, message="r1", node_id=None, context_json={}),
        ]
    )
    await session.commit()

    repo = RunRepository(session)
    all_logs = await repo.list_logs(run_id=run.id)
    assert {row.message for row in all_logs} == {"a1", "b1", "r1"}
    node_a = await repo.list_logs(run_id=run.id, node_id="A")
    assert [row.message for row in node_a] == ["a1"]
    run_only = await repo.list_logs(run_id=run.id, node_id="__run__")
    assert [row.message for row in run_only] == ["r1"]


async def test_recorder_drops_events_after_exit(session: AsyncSession) -> None:
    """A structlog event emitted *after* the recorder exits must not be
    written — the recorder removed itself from the active map and the
    metrics backend was restored."""
    ws = await _seed_workspace(session, slug="rec-after")
    p = Pipeline(workspace_id=ws.id, name="p")
    session.add(p)
    await session.flush()
    pv = PipelineVersion(pipeline_id=p.id, version=1, config_json={"name": "p"}, is_current=True)
    session.add(pv)
    await session.flush()
    run = Run(
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        status=RunStatus.RUNNING,
    )
    session.add(run)
    await session.flush()
    await session.commit()

    factory = _SessionFactoryAdapter(session)
    async with RunRecorder(factory, run.id):
        structlog.get_logger().bind(run_id=str(run.id)).info("inside")
    structlog.get_logger().bind(run_id=str(run.id)).info("after")

    logs = (await session.execute(select(RunLog).where(RunLog.run_id == run.id))).scalars().all()
    messages = [log.message for log in logs]
    assert "inside" in messages
    assert "after" not in messages


# --- end-to-end through the executor ----------------------------------------


async def test_executor_populates_run_logs_and_metrics(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Full happy path: executor runs a real sqlite pipeline, and the
    recorder has populated both ``run_logs`` (lifecycle events) and
    ``run_metrics`` (records_read / records_written / duration_seconds)
    by the time it exits."""
    db_path = _prepare_sqlite(tmp_path, rows=4)
    ws = await _seed_workspace(session, slug="rec-e2e")
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": db_path})
    await _seed_connection(session, workspace_id=ws.id, name="dst", config={"database": db_path})
    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, name FROM seed"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, config=cfg)
    run = await _seed_run(session, workspace_id=ws.id, p=p, pv=pv)
    claimed = await claim_pending_run(session, worker_id="worker-rec")
    assert claimed is not None and claimed.id == run.id
    await session.commit()

    executor = RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="worker-rec"
    )
    await executor.execute(run.id)

    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.SUCCEEDED, refreshed.error_message

    log_rows = (
        (await session.execute(select(RunLog).where(RunLog.run_id == run.id).order_by(RunLog.ts)))
        .scalars()
        .all()
    )
    messages = [r.message for r in log_rows]
    # Build + start + success lifecycle events all land.
    assert "run.build_started" in messages
    assert "run.pipeline_started" in messages
    assert "run.pipeline_succeeded" in messages

    succeeded = next(r for r in log_rows if r.message == "run.pipeline_succeeded")
    assert succeeded.context_json.get("records_read") == 4
    assert succeeded.context_json.get("records_written") == 4

    metric_rows = (
        (await session.execute(select(RunMetric).where(RunMetric.run_id == run.id))).scalars().all()
    )
    metric_names = {m.name for m in metric_rows}
    assert RECORDS_READ_TOTAL in metric_names
    assert RECORDS_WRITTEN_TOTAL in metric_names
    assert DURATION_SECONDS in metric_names

    by_name: dict[str, list[float]] = {}
    for m in metric_rows:
        by_name.setdefault(m.name, []).append(m.value)
    assert sum(by_name[RECORDS_READ_TOTAL]) == pytest.approx(4.0)
    assert sum(by_name[RECORDS_WRITTEN_TOTAL]) == pytest.approx(4.0)
    # Duration is positive but otherwise indeterminate.
    assert all(v > 0 for v in by_name[DURATION_SECONDS])


async def test_executor_logs_failure_when_build_fails(
    session: AsyncSession,
) -> None:
    """A build failure should record ``run.build_failed`` in run_logs with the
    error class so the UI can show what broke without making the user
    cross-reference ``runs.error_class``."""
    ws = await _seed_workspace(session, slug="rec-buildfail")
    # Source connection exists but sink doesn't — _build raises.
    await _seed_connection(session, workspace_id=ws.id, name="src", config={"database": ":memory:"})
    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "select 1"},
        "sink": {"connection": "missing", "table": "out", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, config=cfg)
    run = await _seed_run(session, workspace_id=ws.id, p=p, pv=pv)
    claimed = await claim_pending_run(session, worker_id="worker-bf")
    assert claimed is not None
    await session.commit()

    executor = RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="worker-bf"
    )
    await executor.execute(run.id)

    refreshed = (await session.execute(select(Run).where(Run.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.FAILED

    log_rows = (
        (await session.execute(select(RunLog).where(RunLog.run_id == run.id))).scalars().all()
    )
    messages = [r.message for r in log_rows]
    assert "run.build_started" in messages
    assert "run.build_failed" in messages
    failed = next(r for r in log_rows if r.message == "run.build_failed")
    assert failed.context_json.get("error_class") == "_PipelineBuildError"


# --- periodic drain (live tail) -------------------------------------------


async def test_recorder_flushes_periodically_when_interval_set(
    metadata_engine: AsyncEngine,
) -> None:
    """With ``flush_interval_seconds`` set, run_logs lands mid-run instead of
    only at __aexit__.

    Skips the conftest ``session`` fixture entirely — that fixture wraps an
    outer transaction, and the recorder's separate-connection writes can't
    see uncommitted parent rows through that wrapper. We open our own
    sessionmaker against the same testcontainer engine, seed + assert +
    clean up explicitly. The rows live outside any wrapper, so we delete
    them in a ``finally`` block so later tests in the session don't see
    leftover state.
    """
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        bind=metadata_engine, expire_on_commit=False
    )
    ws_id: UUID | None = None
    pipeline_id: UUID | None = None
    pv_id: UUID | None = None
    run_id: UUID | None = None
    try:
        # Seed via the real factory — these are committed rows visible from
        # any session/connection in the engine pool.
        async with factory() as setup:
            ws = Workspace(name="Rec Live", slug="rec-live-drain", color_hex="#FF3D8B")
            setup.add(ws)
            await setup.flush()
            ws_id = ws.id
            p = Pipeline(workspace_id=ws.id, name="p")
            setup.add(p)
            await setup.flush()
            pipeline_id = p.id
            pv = PipelineVersion(
                pipeline_id=p.id,
                version=1,
                config_json={"name": "p"},
                is_current=True,
            )
            setup.add(pv)
            await setup.flush()
            pv_id = pv.id
            run = Run(
                workspace_id=ws.id,
                pipeline_id=p.id,
                pipeline_version_id=pv.id,
                status=RunStatus.RUNNING,
            )
            setup.add(run)
            await setup.flush()
            run_id = run.id
            await setup.commit()

        async with RunRecorder(factory, run_id, flush_interval_seconds=0.05):
            structlog.get_logger().bind(run_id=str(run_id)).info("mid-run", phase="warm")
            # Wait a hair longer than the drain interval so the timer fires
            # at least once before we check the DB.
            await asyncio.sleep(0.2)

            async with factory() as check:
                mid_logs = (
                    (await check.execute(select(RunLog).where(RunLog.run_id == run_id)))
                    .scalars()
                    .all()
                )
            assert any(
                log.message == "mid-run" for log in mid_logs
            ), "periodic drain should land logs before __aexit__"
    finally:
        # Strip everything we committed so later tests in the session
        # don't see leftover state.
        async with factory() as cleanup:
            if run_id is not None:
                await cleanup.execute(delete(RunLog).where(RunLog.run_id == run_id))
                await cleanup.execute(delete(RunMetric).where(RunMetric.run_id == run_id))
                await cleanup.execute(delete(Run).where(Run.id == run_id))
            if pv_id is not None:
                await cleanup.execute(delete(PipelineVersion).where(PipelineVersion.id == pv_id))
            if pipeline_id is not None:
                await cleanup.execute(delete(Pipeline).where(Pipeline.id == pipeline_id))
            if ws_id is not None:
                await cleanup.execute(delete(Workspace).where(Workspace.id == ws_id))
            await cleanup.commit()
