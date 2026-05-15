"""Pipeline / PipelineVersion / Schedule round-trip tests."""

from __future__ import annotations

import pytest
from etlx_server.db.enums import PipelineMode
from etlx_server.db.models import Pipeline, PipelineVersion, Schedule, Workspace
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_pipeline_with_versions_cascade_delete(session: AsyncSession) -> None:
    ws = Workspace(name="W", slug="pipe-ws")
    session.add(ws)
    await session.flush()
    p = Pipeline(workspace_id=ws.id, name="orders_to_dw", description="hourly upsert")
    session.add(p)
    await session.flush()
    session.add_all(
        [
            PipelineVersion(
                pipeline_id=p.id, version=1, config_json={"mode": "batch"}, is_current=False
            ),
            PipelineVersion(
                pipeline_id=p.id, version=2, config_json={"mode": "batch"}, is_current=True
            ),
        ]
    )
    await session.flush()

    versions = (
        (await session.execute(select(PipelineVersion).where(PipelineVersion.pipeline_id == p.id)))
        .scalars()
        .all()
    )
    assert len(versions) == 2
    current = [v for v in versions if v.is_current]
    assert len(current) == 1 and current[0].version == 2

    # cascade
    await session.delete(p)
    await session.flush()
    remaining = (
        (await session.execute(select(PipelineVersion).where(PipelineVersion.pipeline_id == p.id)))
        .scalars()
        .all()
    )
    assert remaining == []


async def test_pipeline_version_number_unique_per_pipeline(session: AsyncSession) -> None:
    ws = Workspace(name="W", slug="pipe-ws2")
    session.add(ws)
    await session.flush()
    p = Pipeline(workspace_id=ws.id, name="p")
    session.add(p)
    await session.flush()
    session.add(PipelineVersion(pipeline_id=p.id, version=1, config_json={}))
    session.add(PipelineVersion(pipeline_id=p.id, version=1, config_json={}))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_schedule_cron_optional_for_stream(session: AsyncSession) -> None:
    ws = Workspace(name="W", slug="sched-ws")
    session.add(ws)
    await session.flush()
    p = Pipeline(workspace_id=ws.id, name="events_to_kafka")
    session.add(p)
    await session.flush()

    # stream pipeline은 cron 없음 (계속 활성).
    s_stream = Schedule(pipeline_id=p.id, name="continuous", mode=PipelineMode.STREAM)
    # batch는 cron 표현식 보관.
    s_batch = Schedule(
        pipeline_id=p.id, name="hourly", cron_expr="0 * * * *", mode=PipelineMode.BATCH
    )
    session.add_all([s_stream, s_batch])
    await session.flush()
    assert s_stream.cron_expr is None
    assert s_batch.cron_expr == "0 * * * *"
    assert s_batch.is_active is True
