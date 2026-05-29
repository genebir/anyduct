"""Asset-driven auto-materialize dogfooding scenarios (Phase GG, 2026-05-29).

ADR-0037's D1 mechanism auto-enqueues downstream pipelines when their
input asset matches a freshly-materialized upstream asset. The unit
side of this is well covered, but the *interplay* with the catalog —
who triggers whom, what the resulting asset DAG looks like, whether a
failure blocks the chain — is the kind of thing dogfooding catches
better than isolated tests.

Three scenarios, each with sample data:

* **GG1** — happy chain (A → B). Pipeline A writes ``staging``; Pipeline
  B has ``auto_materialize: true`` and reads ``staging``. We trigger A,
  then assert B was enqueued + ran + the catalog shows the full chain.
* **GG2** — fan-out (A → B + C). Two opt-in consumers of the same
  upstream both get triggered by a single materialization.
* **GG3** — failure blocks (A fails → B not enqueued). The trigger only
  fires on the success path, so a failed upstream must leave the
  downstream queue empty.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

import pytest
from etlx_server.assets.repository import AssetRepository
from etlx_server.db.enums import RunStatus
from etlx_server.db.models import Pipeline, Run
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


# ----- helpers ---------------------------------------------------------------


async def _drain_pending_runs(session: AsyncSession, worker_id: str) -> int:
    """Claim + execute every currently-pending run, returning the count.

    The auto-materialize hook enqueues new ``pending`` rows during a
    successful run; we drain those too so the *whole* chain runs in one
    test step.
    """
    executor = RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    )
    executed = 0
    while True:
        claimed = await claim_pending_run(session, worker_id=worker_id)
        if claimed is None:
            break
        await session.commit()
        await executor.execute(claimed.id)
        executed += 1
    return executed


def _query_rows(db_path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(str(db_path))
    try:
        return list(conn.execute(sql).fetchall())
    finally:
        conn.close()


async def _runs_for_pipeline(session: AsyncSession, pipeline_id: UUID) -> list[Run]:
    """Every Run row for a given pipeline, oldest-first."""
    await session.commit()  # commit the test transaction first so we see all rows
    rows = await session.execute(
        select(Run).where(Run.pipeline_id == pipeline_id).order_by(Run.created_at)
    )
    return list(rows.scalars().all())


# ===== GG1: Happy 2-pipeline chain ===========================================


async def test_gg1_auto_materialize_chain_happy_path(session: AsyncSession, tmp_path: Path) -> None:
    """Pipeline A writes ``staging``. Pipeline B (auto_materialize=True)
    reads ``staging`` and writes ``mart``. Triggering A should:

    1. Run A successfully.
    2. Auto-enqueue B because B's input matches A's output asset key.
    3. Run B (after we drain again) and materialize ``mart``.
    4. Leave the catalog with the full chain: raw → staging → mart.
    """
    db_path = tmp_path / "chain.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, amount INTEGER)")
        conn.executemany(
            "INSERT INTO raw VALUES (?, ?)",
            [(1, 100), (2, 250), (3, 50)],
        )
        conn.execute("CREATE TABLE staging (id INTEGER, amount INTEGER)")
        conn.execute("CREATE TABLE mart (id INTEGER, double_amount INTEGER)")
        conn.commit()
    finally:
        conn.close()

    ws = await _seed_workspace(session, slug="gg1-chain")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    p_a_cfg = {
        "name": "stage_a",
        "source": {"connection": "src", "query": "SELECT id, amount FROM raw"},
        "sink": {"connection": "dst", "table": "staging", "mode": "append"},
    }
    # B reads what A just wrote — same connection 'src' so the asset key
    # ``src/staging`` matches A's sink ``dst/staging``? No — different
    # connections give different keys. So we instead point B at the
    # ``dst`` connection where A wrote. The auto-trigger matches on the
    # *exact* asset key, which exposes that asset keys are
    # ``connection/table`` (intentional design from ADR-0036).
    p_b_cfg = {
        "name": "stage_b",
        "auto_materialize": True,
        "source": {
            "connection": "dst",
            "query": "SELECT id, amount * 2 AS double_amount FROM staging",
        },
        "sink": {"connection": "dst", "table": "mart", "mode": "append"},
    }
    p_a, pv_a = await _seed_pipeline(session, workspace_id=ws.id, name="A", config=p_a_cfg)
    p_b, _pv_b = await _seed_pipeline(session, workspace_id=ws.id, name="B", config=p_b_cfg)

    # Only A is manually queued. The auto-trigger should enqueue B.
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p_a.id, pipeline_version_id=pv_a.id
    )
    executed = await _drain_pending_runs(session, "gg1")
    assert executed == 2, f"expected A + auto-triggered B, ran {executed}"

    # B actually consumed staging and wrote mart.
    assert _query_rows(db_path, "SELECT id, double_amount FROM mart ORDER BY id") == [
        (1, 200),
        (2, 500),
        (3, 100),
    ]

    # Each pipeline had exactly one run; B's run was triggered by A.
    runs_a = await _runs_for_pipeline(session, p_a.id)
    runs_b = await _runs_for_pipeline(session, p_b.id)
    assert len(runs_a) == 1
    assert len(runs_b) == 1
    assert runs_a[0].status == RunStatus.SUCCEEDED
    assert runs_b[0].status == RunStatus.SUCCEEDED
    # Trigger lineage stamped on B's run row.
    assert runs_b[0].result_json is not None
    assert runs_b[0].result_json.get("triggered_by_run") == str(runs_a[0].id)
    assert "dst/staging" in runs_b[0].result_json.get("triggered_by_assets", [])

    # Catalog shows the full chain.
    repo = AssetRepository(session)
    assets = {a.asset_key: a for a in await repo.list_for_workspace(workspace_id=ws.id)}
    for must_have in ("src/raw", "dst/staging", "dst/mart"):
        assert must_have in assets, f"missing catalog row: {must_have}"

    async def _ups(asset_key: str) -> set[str]:
        return {a.asset_key for a in await repo.upstream(assets[asset_key].id)}

    assert await _ups("dst/staging") == {"src/raw"}
    # ``dst/mart`` was written by B and pulled from the ``dst`` connection;
    # the same connection's ``staging`` table is the upstream asset.
    assert "dst/staging" in await _ups("dst/mart")


# ===== GG2: Fan-out to multiple opt-in consumers =============================


async def test_gg2_auto_materialize_fanout_two_consumers(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Two opt-in pipelines both read the same upstream asset. A single
    successful materialization should trigger *both*."""
    db_path = tmp_path / "fanout-am.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, amount INTEGER)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, 10), (2, 20)])
        conn.execute("CREATE TABLE shared (id INTEGER, amount INTEGER)")
        conn.execute("CREATE TABLE mart_b (id INTEGER, amount INTEGER)")
        conn.execute("CREATE TABLE mart_c (id INTEGER, doubled INTEGER)")
        conn.commit()
    finally:
        conn.close()

    ws = await _seed_workspace(session, slug="gg2-fanout-am")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    a_cfg = {
        "name": "A",
        "source": {"connection": "src", "query": "SELECT id, amount FROM raw"},
        "sink": {"connection": "dst", "table": "shared", "mode": "append"},
    }
    b_cfg = {
        "name": "B",
        "auto_materialize": True,
        "source": {"connection": "dst", "query": "SELECT id, amount FROM shared"},
        "sink": {"connection": "dst", "table": "mart_b", "mode": "append"},
    }
    c_cfg = {
        "name": "C",
        "auto_materialize": True,
        "source": {
            "connection": "dst",
            "query": "SELECT id, amount * 2 AS doubled FROM shared",
        },
        "sink": {"connection": "dst", "table": "mart_c", "mode": "append"},
    }
    p_a, pv_a = await _seed_pipeline(session, workspace_id=ws.id, name="A", config=a_cfg)
    p_b, _pv_b = await _seed_pipeline(session, workspace_id=ws.id, name="B", config=b_cfg)
    p_c, _pv_c = await _seed_pipeline(session, workspace_id=ws.id, name="C", config=c_cfg)

    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p_a.id, pipeline_version_id=pv_a.id
    )
    executed = await _drain_pending_runs(session, "gg2")
    assert executed == 3, f"expected A + auto B + auto C, ran {executed}"

    # Both downstream sinks got rows.
    assert _query_rows(db_path, "SELECT id, amount FROM mart_b ORDER BY id") == [
        (1, 10),
        (2, 20),
    ]
    assert _query_rows(db_path, "SELECT id, doubled FROM mart_c ORDER BY id") == [
        (1, 20),
        (2, 40),
    ]

    # Both consumers had their B-style triggered run.
    for pipeline_id in (p_b.id, p_c.id):
        runs = await _runs_for_pipeline(session, pipeline_id)
        assert len(runs) == 1
        assert runs[0].status == RunStatus.SUCCEEDED
        assert runs[0].result_json is not None
        assert "triggered_by_run" in runs[0].result_json

    # Catalog shows the fan-out: ``dst/shared`` is upstream of both marts.
    repo = AssetRepository(session)
    assets = {a.asset_key: a for a in await repo.list_for_workspace(workspace_id=ws.id)}
    shared_id = assets["dst/shared"].id
    downstream_keys = {a.asset_key for a in await repo.downstream(shared_id)}
    assert {"dst/mart_b", "dst/mart_c"} <= downstream_keys


# ===== GG3: Failed upstream blocks the chain ================================


async def test_gg3_failed_upstream_does_not_trigger_consumer(
    session: AsyncSession, tmp_path: Path
) -> None:
    """If A fails, the consumer B must *not* be enqueued — the
    auto-materialize hook only fires on the success path. We confirm by
    counting B's runs (should be zero) after running A through its
    failure."""
    db_path = tmp_path / "fail-chain.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER)")
        conn.executemany("INSERT INTO raw VALUES (?)", [(1,), (2,), (3,)])
        conn.execute("CREATE TABLE staging (id INTEGER)")
        conn.execute("CREATE TABLE mart (id INTEGER)")
        conn.commit()
    finally:
        conn.close()

    ws = await _seed_workspace(session, slug="gg3-fail-chain")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    a_cfg = {
        "name": "A_fails",
        "source": {"connection": "src", "query": "SELECT id FROM raw"},
        "transforms": [
            {
                "type": "custom_python",
                "code": (
                    "def transform(record):\n"
                    "    raise RuntimeError('A explodes — B must not trigger')\n"
                ),
            }
        ],
        "sink": {"connection": "dst", "table": "staging", "mode": "append"},
    }
    b_cfg = {
        "name": "B",
        "auto_materialize": True,
        "source": {"connection": "dst", "query": "SELECT id FROM staging"},
        "sink": {"connection": "dst", "table": "mart", "mode": "append"},
    }
    p_a, pv_a = await _seed_pipeline(session, workspace_id=ws.id, name="A", config=a_cfg)
    p_b, _pv_b = await _seed_pipeline(session, workspace_id=ws.id, name="B", config=b_cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p_a.id, pipeline_version_id=pv_a.id
    )

    executed = await _drain_pending_runs(session, "gg3")
    # Only A ran; B was never enqueued because A failed.
    assert executed == 1

    # A is failed; B has zero runs.
    runs_a = await _runs_for_pipeline(session, p_a.id)
    runs_b = await _runs_for_pipeline(session, p_b.id)
    assert len(runs_a) == 1 and runs_a[0].status == RunStatus.FAILED
    assert runs_b == []

    # Sink stayed empty; catalog has no rows because the failure path
    # skips the lineage persistence (covered by Scenario F, here we
    # verify the chain-blocking property specifically).
    assert _query_rows(db_path, "SELECT id FROM staging") == []
    assert _query_rows(db_path, "SELECT id FROM mart") == []
    repo = AssetRepository(session)
    assets = {a.asset_key for a in await repo.list_for_workspace(workspace_id=ws.id)}
    assert "dst/staging" not in assets
    assert "dst/mart" not in assets


# ===== LL1: Cycle prevention ================================================


async def test_ll1_auto_materialize_cycle_is_broken_by_trigger_chain(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Phase LL (2026-05-29, ADR-0056): two ``auto_materialize`` pipelines
    that read each other's outputs would loop forever without a safeguard.
    The worker's ``trigger_chain`` (ADR-0037) records every pipeline run
    in the current chain and skips re-enqueueing one that's already
    upstream — that's the cycle break.

    Setup: ``A`` writes ``mart`` and reads ``staging``; ``B`` writes
    ``staging`` and reads ``mart``. Both are ``auto_materialize: true``.

    Trigger ``A`` once. The expected outcome:

    1. A runs (writes ``mart``).
    2. The auto-trigger fires for B (it reads ``mart``); B is enqueued
       with ``trigger_chain=[A]``.
    3. B runs (writes ``staging``).
    4. The auto-trigger fires again, this time looking for pipelines
       that read ``staging`` — A is the candidate, but A is already in
       B's ``trigger_chain``, so the worker skips it.
    5. Final tally: exactly 1 run on A, 1 run on B.

    Without the cycle break the queue would never drain.
    """
    db_path = tmp_path / "cycle.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE staging (id INTEGER, label TEXT)")
        conn.execute("CREATE TABLE mart (id INTEGER, label TEXT)")
        # Seed both with rows so neither read returns empty.
        conn.executemany("INSERT INTO staging VALUES (?, ?)", [(1, "from-staging-seed")])
        conn.executemany("INSERT INTO mart VALUES (?, ?)", [(2, "from-mart-seed")])
        conn.commit()
    finally:
        conn.close()

    ws = await _seed_workspace(session, slug="ll1-cycle")
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    a_cfg = {
        "name": "A",
        "auto_materialize": True,
        "source": {"connection": "dst", "query": "SELECT id, label FROM staging"},
        "sink": {"connection": "dst", "table": "mart", "mode": "append"},
    }
    b_cfg = {
        "name": "B",
        "auto_materialize": True,
        "source": {"connection": "dst", "query": "SELECT id, label FROM mart"},
        "sink": {"connection": "dst", "table": "staging", "mode": "append"},
    }
    p_a, pv_a = await _seed_pipeline(session, workspace_id=ws.id, name="A", config=a_cfg)
    p_b, _ = await _seed_pipeline(session, workspace_id=ws.id, name="B", config=b_cfg)

    # Manually trigger A. The chain should be: A runs → B auto-queued →
    # B runs → A skipped because it's in B's trigger_chain.
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p_a.id, pipeline_version_id=pv_a.id
    )
    executed = await _drain_pending_runs(session, "ll1")
    assert executed == 2, f"expected exactly A + B (one of each, cycle broken), executed {executed}"

    runs_a = await _runs_for_pipeline(session, p_a.id)
    runs_b = await _runs_for_pipeline(session, p_b.id)
    assert len(runs_a) == 1, f"A ran {len(runs_a)} times — cycle break failed"
    assert len(runs_b) == 1, f"B ran {len(runs_b)} times — cycle break failed"

    # B's trigger_chain stamps A as the originator. The chain B was
    # queued with carries A's pipeline id so the worker can reject any
    # further attempt to enqueue A.
    assert runs_b[0].result_json is not None
    chain = runs_b[0].result_json.get("trigger_chain")
    assert chain == [str(p_a.id)], f"expected trigger_chain=[A.id] on B's run, got {chain!r}"


# ----- guard rail: silence "unused import" warning for Pipeline ----------
# We use the type indirectly via the SQLAlchemy join in helpers; this keeps
# linters happy without polluting the test module body.
_ = Pipeline
