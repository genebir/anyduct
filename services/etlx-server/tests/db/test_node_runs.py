"""node_runs queue — dependency-counter claim flow (ADR-0041, Phase H1)."""

from __future__ import annotations

from etlx_server.db.enums import RunStatus
from etlx_server.db.models import Pipeline, PipelineVersion, Run, Workspace
from etlx_server.node_runs import NodeRunRepository, NodeSpec
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_run(session: AsyncSession, *, slug: str) -> Run:
    ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
    session.add(ws)
    await session.flush()
    pipeline = Pipeline(workspace_id=ws.id, name="p")
    session.add(pipeline)
    await session.flush()
    version = PipelineVersion(
        pipeline_id=pipeline.id, version=1, config_json={"name": "p"}, is_current=True
    )
    session.add(version)
    await session.flush()
    run = Run(
        workspace_id=ws.id,
        pipeline_id=pipeline.id,
        pipeline_version_id=version.id,
        status=RunStatus.RUNNING,
    )
    session.add(run)
    await session.flush()
    return run


async def test_create_for_run_seeds_pending_deps(session: AsyncSession) -> None:
    run = await _seed_run(session, slug="nr-create")
    repo = NodeRunRepository()
    await repo.create_for_run(
        session,
        run.id,
        [NodeSpec("a", "source", []), NodeSpec("b", "transform", ["a"])],
    )
    rows = await repo.list_for_run(session, run.id)
    assert [r.node_id for r in rows] == ["a", "b"]
    assert {r.node_id: r.pending_deps for r in rows} == {"a": 0, "b": 1}
    assert all(r.status == RunStatus.PENDING for r in rows)


async def test_only_root_is_claimable_initially(session: AsyncSession) -> None:
    run = await _seed_run(session, slug="nr-root")
    repo = NodeRunRepository()
    await repo.create_for_run(
        session,
        run.id,
        [NodeSpec("a", "source", []), NodeSpec("b", "sink", ["a"])],
    )
    first = await repo.claim_ready(session, worker_id="w1")
    assert first is not None
    assert first.node_id == "a"
    assert first.status == RunStatus.RUNNING
    assert first.worker_id == "w1"
    assert first.attempt == 1
    # 'b' is still blocked (pending_deps=1) → nothing else to claim.
    assert await repo.claim_ready(session, worker_id="w2") is None


async def test_diamond_dependency_flow(session: AsyncSession) -> None:
    run = await _seed_run(session, slug="nr-diamond")
    repo = NodeRunRepository()
    # a → b, a → c, {b,c} → d
    await repo.create_for_run(
        session,
        run.id,
        [
            NodeSpec("a", "source", []),
            NodeSpec("b", "transform", ["a"]),
            NodeSpec("c", "transform", ["a"]),
            NodeSpec("d", "join", ["b", "c"]),
        ],
    )

    node_a = await repo.claim_ready(session, worker_id="w")
    assert node_a is not None and node_a.node_id == "a"
    await repo.mark_succeeded(session, node_a, records_read=3, records_written=3)

    # b and c are now ready; d stays blocked until both finish.
    claimed: list[str] = []
    while (nr := await repo.claim_ready(session, worker_id="w")) is not None:
        claimed.append(nr.node_id)
        if nr.node_id == "d":
            break
        await repo.mark_succeeded(session, nr)
    assert claimed == ["b", "c", "d"]

    rows = {r.node_id: r for r in await repo.list_for_run(session, run.id)}
    assert rows["d"].pending_deps == 0
    assert rows["a"].records_written == 3


async def test_failed_node_blocks_downstream(session: AsyncSession) -> None:
    run = await _seed_run(session, slug="nr-fail")
    repo = NodeRunRepository()
    await repo.create_for_run(
        session,
        run.id,
        [NodeSpec("a", "source", []), NodeSpec("b", "sink", ["a"])],
    )
    node_a = await repo.claim_ready(session, worker_id="w")
    assert node_a is not None
    await repo.mark_failed(session, node_a, error_class="BoomError", error_message="boom")

    # downstream 'b' never becomes ready — its dep didn't succeed.
    assert await repo.claim_ready(session, worker_id="w") is None
    rows = {r.node_id: r for r in await repo.list_for_run(session, run.id)}
    assert rows["a"].status == RunStatus.FAILED
    assert rows["a"].error_class == "BoomError"
    assert rows["b"].pending_deps == 1
    assert rows["b"].status == RunStatus.PENDING
