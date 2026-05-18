"""Stream worker lifecycle (Step 9.4).

Covers:

* Active stream schedules → spawn Run rows in ``running`` status.
* Inactive / batch schedules → ignored.
* Pipeline mode mismatch (schedule says stream, version says batch) →
  Run row stamped ``failed`` with ``error_class='ModeMismatch'``.
* Cancellation on schedule deactivation: in-flight task cancelled and
  its Run row stamped ``cancelled``.
* Worker shutdown stamps every in-flight stream ``cancelled``.

Uses a monkey-patched :func:`build_pipeline` so we don't need a real
Kafka testcontainer for this slice. The fake pipeline implements
``arun_stream`` as an asyncio sleep that respects cancellation, which
is exactly the lifecycle the worker cares about.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from etlx_server.db.enums import PipelineMode, RunStatus
from etlx_server.db.models import (
    Connection,
    Pipeline,
    PipelineVersion,
    Run,
    Schedule,
    Workspace,
)
from etlx_server.worker.stream import StreamWorker
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from etl_plugins.config.secrets import StaticSecretBackend
from etl_plugins.core.connector import Connector
from etl_plugins.core.pipeline import RunResult

pytestmark = pytest.mark.asyncio


class _FakeStreamConnector(Connector):
    """Minimal connector stub — `connect`/`close` are no-ops."""

    def connect(self) -> None:  # pragma: no cover — exercised indirectly
        pass

    def close(self) -> None:  # pragma: no cover
        pass

    def health_check(self) -> tuple[bool, str | None]:  # pragma: no cover
        return True, None


class _FakeStreamPipeline:
    """Stand-in for a core ``Pipeline`` whose ``arun_stream`` sleeps until
    cancellation.

    ``records_read``/``records_written`` are exposed so we can confirm the
    worker propagates them onto the Run row when ``arun_stream`` returns
    normally.
    """

    def __init__(self, name: str = "fake", *, ttl_seconds: float = 30.0) -> None:
        self.name = name
        self.mode = "stream"
        self._ttl = ttl_seconds

    async def arun_stream(self, _ctx: Any, *, connectors: Any = None) -> RunResult:
        await asyncio.sleep(self._ttl)
        return RunResult(
            run_id="fake-run-id",
            pipeline_name=self.name,
            success=True,
            records_read=0,
            records_written=0,
            duration_seconds=self._ttl,
        )


@pytest_asyncio.fixture
async def real_factory(
    metadata_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Fresh sessionmaker bound to the testcontainer engine.

    Stream worker writes from a separate task; we use a non-wrapped factory
    so the rows commit normally and the worker can read them back.
    """
    return async_sessionmaker(bind=metadata_engine, expire_on_commit=False)


# --- helpers ---------------------------------------------------------------


def _stream_config(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "mode": "stream",
        "source": {"connection": "src", "topic": "in"},
        "sink": {"connection": "dst", "topic": "out"},
    }


def _batch_config(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "mode": "batch",
        "source": {"connection": "src", "query": "select 1"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }


async def _seed_workspace_with_pipeline(
    factory: async_sessionmaker[AsyncSession],
    *,
    slug: str,
    config: dict[str, Any],
    schedule_mode: PipelineMode,
    schedule_active: bool = True,
) -> dict[str, UUID]:
    """Seed a workspace + pipeline (config_json) + matching schedule.

    Commits everything via the real factory so the stream worker (which
    opens its own sessions) sees the rows.
    """
    async with factory() as s:
        ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
        s.add(ws)
        await s.flush()
        # Seed both connections referenced in the config so build doesn't
        # complain. Type is irrelevant — build is mocked in these tests.
        for name in ("src", "dst"):
            s.add(
                Connection(
                    workspace_id=ws.id,
                    name=name,
                    type="kafka",
                    config_json={"bootstrap_servers": "localhost:9092"},
                    secret_refs=[],
                )
            )
        p = Pipeline(workspace_id=ws.id, name=config["name"])
        s.add(p)
        await s.flush()
        pv = PipelineVersion(pipeline_id=p.id, version=1, config_json=config, is_current=True)
        s.add(pv)
        await s.flush()
        sched = Schedule(
            pipeline_id=p.id,
            name=f"sched-{slug}",
            cron_expr=None,
            mode=schedule_mode,
            is_active=schedule_active,
            config_overrides={},
        )
        s.add(sched)
        await s.flush()
        await s.commit()
        return {
            "workspace_id": ws.id,
            "pipeline_id": p.id,
            "version_id": pv.id,
            "schedule_id": sched.id,
        }


async def _cleanup(factory: async_sessionmaker[AsyncSession], seed: dict[str, UUID]) -> None:
    async with factory() as s:
        await s.execute(delete(Run).where(Run.schedule_id == seed["schedule_id"]))
        await s.execute(delete(Schedule).where(Schedule.id == seed["schedule_id"]))
        await s.execute(delete(PipelineVersion).where(PipelineVersion.id == seed["version_id"]))
        await s.execute(delete(Pipeline).where(Pipeline.id == seed["pipeline_id"]))
        await s.execute(delete(Connection).where(Connection.workspace_id == seed["workspace_id"]))
        await s.execute(delete(Workspace).where(Workspace.id == seed["workspace_id"]))
        await s.commit()


def _patch_build(monkeypatch: pytest.MonkeyPatch, *, ttl_seconds: float = 30.0) -> None:
    """Replace ``build_pipeline`` + ``build_connector`` inside the stream
    module with stubs that don't need real Kafka."""
    import etlx_server.worker.stream as stream_mod

    monkeypatch.setattr(
        stream_mod,
        "build_pipeline",
        lambda cfg, connectors: (_FakeStreamPipeline(cfg.name, ttl_seconds=ttl_seconds), None),
    )
    monkeypatch.setattr(
        stream_mod,
        "build_connector",
        lambda name, cfg: _FakeStreamConnector(),
    )


# --- tests ------------------------------------------------------------------


async def test_stream_worker_starts_active_stream_schedule(
    real_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An active stream schedule yields a Run row in ``running`` status."""
    _patch_build(monkeypatch)
    seed = await _seed_workspace_with_pipeline(
        real_factory,
        slug="sw-start",
        config=_stream_config("p"),
        schedule_mode=PipelineMode.STREAM,
    )
    worker = StreamWorker(
        real_factory,
        StaticSecretBackend(),
        worker_id="stream-test",
        tick_interval_seconds=0.05,
        log_flush_interval_seconds=None,
    )

    async def _stop_soon() -> None:
        await asyncio.sleep(0.3)
        worker.stop()

    try:
        await asyncio.gather(worker.run(), _stop_soon())

        async with real_factory() as s:
            runs = (
                (await s.execute(select(Run).where(Run.schedule_id == seed["schedule_id"])))
                .scalars()
                .all()
            )
        # Exactly one Run was spawned even though the worker ticked
        # multiple times — _inflight prevents duplicates.
        assert len(runs) == 1
        run = runs[0]
        assert run.pipeline_id == seed["pipeline_id"]
        assert run.workspace_id == seed["workspace_id"]
        # Worker shutdown stamps in-flight streams ``cancelled``.
        assert run.status == RunStatus.CANCELLED
        assert run.error_class == "StreamCancelled"
        assert run.started_at is not None
        assert run.finished_at is not None
    finally:
        await _cleanup(real_factory, seed)


async def test_stream_worker_ignores_inactive_and_batch_schedules(
    real_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_build(monkeypatch)
    inactive = await _seed_workspace_with_pipeline(
        real_factory,
        slug="sw-inactive",
        config=_stream_config("p1"),
        schedule_mode=PipelineMode.STREAM,
        schedule_active=False,
    )
    batch = await _seed_workspace_with_pipeline(
        real_factory,
        slug="sw-batch",
        config=_batch_config("p2"),
        schedule_mode=PipelineMode.BATCH,
    )
    worker = StreamWorker(
        real_factory,
        StaticSecretBackend(),
        worker_id="stream-test-ignore",
        tick_interval_seconds=0.05,
        log_flush_interval_seconds=None,
    )

    async def _stop_soon() -> None:
        await asyncio.sleep(0.2)
        worker.stop()

    try:
        await asyncio.gather(worker.run(), _stop_soon())

        async with real_factory() as s:
            inactive_runs = (
                (await s.execute(select(Run).where(Run.schedule_id == inactive["schedule_id"])))
                .scalars()
                .all()
            )
            batch_runs = (
                (await s.execute(select(Run).where(Run.schedule_id == batch["schedule_id"])))
                .scalars()
                .all()
            )
        assert inactive_runs == []
        assert batch_runs == []
    finally:
        await _cleanup(real_factory, inactive)
        await _cleanup(real_factory, batch)


async def test_stream_worker_records_mode_mismatch_failure(
    real_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A schedule marked stream pointing at a batch pipeline version
    surfaces ``error_class='ModeMismatch'`` on the Run row instead of
    silently spinning."""
    _patch_build(monkeypatch)
    # Schedule says stream, but the PipelineVersion config_json is batch.
    seed = await _seed_workspace_with_pipeline(
        real_factory,
        slug="sw-mismatch",
        config=_batch_config("p3"),
        schedule_mode=PipelineMode.STREAM,
    )
    worker = StreamWorker(
        real_factory,
        StaticSecretBackend(),
        worker_id="stream-test-mismatch",
        tick_interval_seconds=0.05,
        log_flush_interval_seconds=None,
    )

    async def _stop_soon() -> None:
        await asyncio.sleep(0.3)
        worker.stop()

    try:
        await asyncio.gather(worker.run(), _stop_soon())

        async with real_factory() as s:
            runs = (
                (await s.execute(select(Run).where(Run.schedule_id == seed["schedule_id"])))
                .scalars()
                .all()
            )
        assert len(runs) == 1
        run = runs[0]
        assert run.status == RunStatus.FAILED
        assert run.error_class == "ModeMismatch"
        assert "non-stream" in (run.error_message or "")
    finally:
        await _cleanup(real_factory, seed)


async def test_stream_worker_stops_schedule_when_deactivated(
    real_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flipping a schedule's ``is_active`` to False mid-run cancels the
    in-flight task and stamps the Run row ``cancelled``."""
    _patch_build(monkeypatch, ttl_seconds=30.0)
    seed = await _seed_workspace_with_pipeline(
        real_factory,
        slug="sw-deact",
        config=_stream_config("p4"),
        schedule_mode=PipelineMode.STREAM,
    )
    worker = StreamWorker(
        real_factory,
        StaticSecretBackend(),
        worker_id="stream-test-deact",
        tick_interval_seconds=0.05,
        log_flush_interval_seconds=None,
    )

    async def _deactivate_then_stop() -> None:
        # Let the worker tick start the stream.
        await asyncio.sleep(0.15)
        async with real_factory() as s:
            sched = (
                await s.execute(select(Schedule).where(Schedule.id == seed["schedule_id"]))
            ).scalar_one()
            sched.is_active = False
            await s.commit()
        # Give the worker another tick to notice + cancel.
        await asyncio.sleep(0.25)
        worker.stop()

    try:
        await asyncio.gather(worker.run(), _deactivate_then_stop())

        async with real_factory() as s:
            runs = (
                (await s.execute(select(Run).where(Run.schedule_id == seed["schedule_id"])))
                .scalars()
                .all()
            )
        assert len(runs) == 1
        assert runs[0].status == RunStatus.CANCELLED
        assert runs[0].error_class == "StreamCancelled"
    finally:
        await _cleanup(real_factory, seed)


async def test_stream_worker_pre_set_stop_exits_immediately(
    real_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``worker.stop()`` before ``worker.run()`` returns without ticking."""
    _patch_build(monkeypatch)
    worker = StreamWorker(
        real_factory,
        StaticSecretBackend(),
        worker_id="stream-test-stop",
        tick_interval_seconds=0.05,
        log_flush_interval_seconds=None,
    )
    worker.stop()
    # No timeout needed — the while-loop short-circuits on the first check.
    await worker.run()


async def test_stream_worker_skips_schedule_without_current_version(
    real_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A schedule attached to a pipeline that somehow has no current version
    should log + skip rather than creating an orphan Run row."""
    _patch_build(monkeypatch)
    # Hand-roll the seed since the helper always creates a version.
    async with real_factory() as s:
        ws = Workspace(name="Sw Nov", slug="sw-no-version", color_hex="#FF3D8B")
        s.add(ws)
        await s.flush()
        p = Pipeline(workspace_id=ws.id, name="p5")
        s.add(p)
        await s.flush()
        sched = Schedule(
            pipeline_id=p.id,
            name="sched-novers",
            cron_expr=None,
            mode=PipelineMode.STREAM,
            is_active=True,
            config_overrides={},
        )
        s.add(sched)
        await s.flush()
        await s.commit()
        ws_id, p_id, sched_id = ws.id, p.id, sched.id

    worker = StreamWorker(
        real_factory,
        StaticSecretBackend(),
        worker_id="stream-test-nov",
        tick_interval_seconds=0.05,
        log_flush_interval_seconds=None,
    )

    async def _stop_soon() -> None:
        await asyncio.sleep(0.2)
        worker.stop()

    try:
        await asyncio.gather(worker.run(), _stop_soon())
        async with real_factory() as s:
            runs = (await s.execute(select(Run).where(Run.schedule_id == sched_id))).scalars().all()
        assert runs == []
    finally:
        async with real_factory() as s:
            await s.execute(delete(Schedule).where(Schedule.id == sched_id))
            await s.execute(delete(Pipeline).where(Pipeline.id == p_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()
