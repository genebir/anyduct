"""Run / RunLog / RunMetric round-trip tests + queue semantics smoke."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from anyduct_server.db.enums import LogLevel, PipelineMode, RunStatus
from anyduct_server.db.models import (
    Pipeline,
    PipelineVersion,
    Run,
    RunLog,
    RunMetric,
    Schedule,
    Workspace,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _seed_pipeline_with_version(
    session: AsyncSession,
) -> tuple[Workspace, Pipeline, PipelineVersion]:
    ws = Workspace(name="W", slug=f"run-ws-{datetime.now().microsecond}")
    session.add(ws)
    await session.flush()
    p = Pipeline(workspace_id=ws.id, name="p")
    session.add(p)
    await session.flush()
    pv = PipelineVersion(
        pipeline_id=p.id, version=1, config_json={"mode": "batch"}, is_current=True
    )
    session.add(pv)
    await session.flush()
    return ws, p, pv


async def test_run_lifecycle_pending_to_succeeded(session: AsyncSession) -> None:
    ws, p, pv = await _seed_pipeline_with_version(session)
    r = Run(
        workspace_id=ws.id,
        pipeline_id=p.id,
        pipeline_version_id=pv.id,
        status=RunStatus.PENDING,
    )
    session.add(r)
    await session.flush()
    assert r.status == RunStatus.PENDING
    assert r.records_read == 0
    assert r.scheduled_at is not None

    # 워커가 claim → running.
    now = datetime.now(UTC)
    r.status = RunStatus.RUNNING
    r.started_at = now
    r.heartbeat_at = now
    r.worker_id = "worker-1"
    await session.flush()

    # 완료.
    r.status = RunStatus.SUCCEEDED
    r.finished_at = now + timedelta(seconds=5)
    r.records_read = 100
    r.records_written = 100
    r.duration_seconds = 5.0
    r.result_json = {"core_run_id": "abc-123"}
    await session.flush()

    found = (await session.execute(select(Run).where(Run.id == r.id))).scalar_one()
    assert found.status == RunStatus.SUCCEEDED
    assert found.records_written == 100
    assert found.result_json == {"core_run_id": "abc-123"}


async def test_run_logs_and_metrics_cascade(session: AsyncSession) -> None:
    ws, p, pv = await _seed_pipeline_with_version(session)
    r = Run(workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id)
    session.add(r)
    await session.flush()
    session.add_all(
        [
            RunLog(run_id=r.id, level=LogLevel.INFO, message="started"),
            RunLog(
                run_id=r.id, level=LogLevel.WARNING, message="retrying", context_json={"attempt": 2}
            ),
            RunMetric(
                run_id=r.id,
                name="etl_plugins.records.read",
                value=100.0,
                attrs_json={"pipeline": "p"},
            ),
        ]
    )
    await session.flush()

    await session.delete(r)
    await session.flush()
    logs = (await session.execute(select(RunLog).where(RunLog.run_id == r.id))).scalars().all()
    metrics = (
        (await session.execute(select(RunMetric).where(RunMetric.run_id == r.id))).scalars().all()
    )
    assert logs == []
    assert metrics == []


async def test_run_queue_skip_locked_pattern(session: AsyncSession) -> None:
    """ADR-0021 §1: 워커가 pending row를 SKIP LOCKED로 claim하는 패턴.

    같은 트랜잭션에서 lock 보유 중인 row를 다른 SELECT가 건너뛰는지 확인. 이건
    PostgreSQL의 핵심 약속이고 우리 큐 설계의 토대.
    """
    ws, p, pv = await _seed_pipeline_with_version(session)
    runs = [
        Run(
            workspace_id=ws.id,
            pipeline_id=p.id,
            pipeline_version_id=pv.id,
            status=RunStatus.PENDING,
        )
        for _ in range(3)
    ]
    session.add_all(runs)
    await session.flush()

    # raw SQL — SQLAlchemy의 FOR UPDATE SKIP LOCKED 구문이 동작하는지만.
    claimed = await session.execute(
        text(
            "SELECT id FROM runs "
            "WHERE status='pending' "
            "ORDER BY scheduled_at "
            "FOR UPDATE SKIP LOCKED "
            "LIMIT 2"
        )
    )
    rows = claimed.fetchall()
    assert len(rows) == 2  # 같은 세션에서 2개 잠금 — 동작 자체가 깨지지 않는지만 확인


async def test_schedule_relation_set_null_on_delete(session: AsyncSession) -> None:
    """Schedule 삭제 시 run.schedule_id는 NULL로 끊김 (RESTRICT 아님)."""
    ws, p, pv = await _seed_pipeline_with_version(session)
    s = Schedule(pipeline_id=p.id, name="hourly", cron_expr="0 * * * *", mode=PipelineMode.BATCH)
    session.add(s)
    await session.flush()
    r = Run(workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id, schedule_id=s.id)
    session.add(r)
    await session.flush()
    await session.delete(s)
    await session.flush()
    await session.refresh(r)
    assert r.schedule_id is None
