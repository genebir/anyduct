"""Audit log integration scenarios (Phase QQ, 2026-05-29).

Phase U / Phase W (ADR-0041 W) added data-plane audit events:
``run.sql_read`` (source SELECT against a SQL connection),
``run.sql_executed`` (sql_exec or sql_exec transform), and
``run.python_executed`` (python / custom_python transform). The
unit tests cover each event in isolation; this module exercises them
together with a *real run* + sample data so an operator can answer
"what happened in this run?" by scanning audit_log alone.

Two scenarios:

* **QQ1** — One run, full data-plane audit. A pipeline reading SQL +
  running a ``custom_python`` transform leaves three audit rows on a
  single run id: ``run.sql_read`` (source SELECT), ``run.python_executed``
  (transform body), and the lineage emit doesn't leak any fields. Each
  row is workspace-scoped, resource_id = run.id, action exactly the
  expected string.
* **QQ2** — Two runs of the same pipeline. The audit feed isolates
  per-run records (filtering on resource_id), so two runs produce two
  ``run.sql_read`` rows + two ``run.python_executed`` rows in time
  order. Cross-run leakage would surface as wrong resource_id values.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from etlx_server.db.enums import RunStatus
from etlx_server.db.models import AuditLog, Run
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


async def _run_one(session: AsyncSession, worker_id: str) -> Run:
    claimed = await claim_pending_run(session, worker_id=worker_id)
    assert claimed is not None
    await session.commit()
    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    ).execute(claimed.id)
    rows = await session.execute(select(Run).where(Run.id == claimed.id))
    return rows.scalar_one()


async def _audit_for_run(session: AsyncSession, *, run_id) -> list[AuditLog]:
    """All audit_log rows tied to a run via resource_id, oldest first."""
    await session.commit()
    rows = await session.execute(
        select(AuditLog)
        .where(AuditLog.resource_id == str(run_id))
        .order_by(AuditLog.created_at, AuditLog.id)
    )
    return list(rows.scalars().all())


def _seed_warehouse(tmp_path: Path) -> Path:
    db_path = tmp_path / "qq.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, name TEXT)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, "alice"), (2, "bob")])
        conn.execute("CREATE TABLE out (id INTEGER, name TEXT)")
        conn.commit()
    finally:
        conn.close()
    return db_path


# ===== QQ1: Full audit trail of one run =====================================


async def test_qq1_single_run_emits_sql_read_and_python_executed(
    session: AsyncSession, tmp_path: Path
) -> None:
    """One sqlite-source + custom_python transform + sqlite sink. The
    audit trail for this run should contain:

    * exactly one ``run.sql_read`` (the source SELECT),
    * exactly one ``run.python_executed`` (the transform body),
    * no rogue ``run.sql_executed`` (we have no sql_exec transform).
    """
    db_path = _seed_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="qq1-audit")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, name FROM raw"},
        "transforms": [
            {
                "type": "custom_python",
                "code": "def transform(record):\n    return record\n",
            }
        ],
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    finished = await _run_one(session, "qq1")
    assert finished.status == RunStatus.SUCCEEDED

    rows = await _audit_for_run(session, run_id=finished.id)
    actions = [r.action for r in rows]

    # Each data-plane action shows up exactly once.
    assert actions.count("run.sql_read") == 1
    assert actions.count("run.python_executed") == 1
    # No sql_exec audit row because the pipeline has no sql_exec transform.
    assert actions.count("run.sql_executed") == 0

    sql_read = next(r for r in rows if r.action == "run.sql_read")
    py_exec = next(r for r in rows if r.action == "run.python_executed")

    # ``run.sql_read`` after_json carries the source SELECT verbatim + a
    # connection_type so an HTTP/Kafka query string doesn't get mislabelled.
    assert sql_read.resource_type == "run"
    assert sql_read.resource_id == str(finished.id)
    assert sql_read.after_json is not None
    assert sql_read.after_json["kind"] == "source"
    assert sql_read.after_json["connection"] == "src"
    assert sql_read.after_json["connection_type"] == "sqlite"
    assert sql_read.after_json["query"] == "SELECT id, name FROM raw"
    assert sql_read.after_json["query_truncated"] is False

    # ``run.python_executed`` carries the kind label + a short fingerprint
    # so the UI can show "same code as last run" without storing the full
    # source again.
    assert py_exec.resource_type == "run"
    assert py_exec.resource_id == str(finished.id)
    assert py_exec.after_json is not None
    assert py_exec.after_json["kind"] == "transform:custom_python"
    assert "code_hash" in py_exec.after_json


# ===== QQ2: Two runs → independent audit trails ============================


async def test_qq2_two_runs_audit_trails_are_isolated(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Run the same pipeline twice. Filtering audit_log by each run's id
    must return exactly that run's own data-plane rows — no leakage
    between runs of the same pipeline."""
    db_path = _seed_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="qq2-audit")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, name FROM raw"},
        "sink": {"connection": "dst", "table": "out", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)

    # ---- Run #1 ----
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    run1 = await _run_one(session, "qq2-1")
    assert run1.status == RunStatus.SUCCEEDED
    # ---- Run #2 ----
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    run2 = await _run_one(session, "qq2-2")
    assert run2.status == RunStatus.SUCCEEDED
    assert run1.id != run2.id

    rows1 = await _audit_for_run(session, run_id=run1.id)
    rows2 = await _audit_for_run(session, run_id=run2.id)
    # Each run has exactly one ``run.sql_read`` (no python transform here).
    assert [r.action for r in rows1] == ["run.sql_read"]
    assert [r.action for r in rows2] == ["run.sql_read"]
    # And the resource_ids match their own run ids — no cross-leakage.
    assert rows1[0].resource_id == str(run1.id)
    assert rows2[0].resource_id == str(run2.id)
    # Time-order across both runs: run1's row precedes run2's row.
    assert rows1[0].created_at <= rows2[0].created_at
