"""Workspace variables (``${var.name}``) dogfooding scenarios (Phase MM, 2026-05-29).

ADR-0041 V2 lets each workspace define name-value pairs that the worker
substitutes into pipeline configs before running them. The canonical
multi-tenant pattern is: same pipeline config, different variable
*values* per workspace (``target_table`` = ``"marts_prod"`` here,
``"marts_dev"`` there). Catalog asset keys then differ across
workspaces even though the user-authored config text is byte-identical.

This scenario seeds two workspaces with identical pipelines and
different ``target_table`` variable values, runs both, and verifies:

* the sink data landed in *each ws's* expected table,
* each ws's catalog has the resolved sink table as an asset key,
* neither ws sees the other's resolved key (workspace boundary holds,
  same as Phase HH).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from anyduct_server.assets.repository import AssetRepository
from anyduct_server.db.models import WorkspaceVariable
from anyduct_server.worker.claim import claim_pending_run
from anyduct_server.worker.executor import RunExecutor
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


async def _seed_variable(
    session: AsyncSession, *, workspace_id, name: str, value
) -> WorkspaceVariable:
    """Insert one workspace variable. Worker reads via
    ``WorkspaceVariableRepository.as_dict`` and feeds it as ``extra=`` to
    ``resolve_config_variables``."""
    v = WorkspaceVariable(workspace_id=workspace_id, name=name, value_json=value)
    session.add(v)
    await session.flush()
    return v


async def _run_pending(session: AsyncSession, worker_id: str) -> None:
    claimed = await claim_pending_run(session, worker_id=worker_id)
    assert claimed is not None
    await session.commit()
    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    ).execute(claimed.id)


def _seed_warehouse_with_two_targets(tmp_path: Path, prefix: str) -> Path:
    """Single sqlite file with ``raw`` + two possible sink tables. We
    create both target tables ahead of time so each workspace's
    ``${var.target_table}`` resolves to one or the other."""
    db_path = tmp_path / f"{prefix}.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, value INTEGER)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, 100), (2, 200), (3, 300)])
        conn.execute("CREATE TABLE marts_prod (id INTEGER, value INTEGER)")
        conn.execute("CREATE TABLE marts_dev (id INTEGER, value INTEGER)")
        conn.commit()
    finally:
        conn.close()
    return db_path


# ===== MM1: Same config, different var values, distinct catalogs ============


async def test_mm1_same_config_different_var_values_resolve_per_workspace(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Two workspaces register the *same* pipeline config text — the
    sink's ``table`` field is ``"${var.target_table}"``. Each workspace
    defines that variable to a different real table name. After both
    runs, each ws's catalog should show its own resolved key, and the
    sqlite tables should have rows in their respective sink only.
    """
    db_a = _seed_warehouse_with_two_targets(tmp_path / "a", "a") if False else None
    # Use one shared file under tmp_path to keep the data plane simple.
    db_path = _seed_warehouse_with_two_targets(tmp_path, "shared")
    _ = db_a  # silence unused

    ws_prod = await _seed_workspace(session, slug="mm1-prod")
    ws_dev = await _seed_workspace(session, slug="mm1-dev")
    for ws in (ws_prod, ws_dev):
        await _seed_connection(
            session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
        )
        await _seed_connection(
            session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
        )

    # Different variable values; same pipeline config text.
    await _seed_variable(session, workspace_id=ws_prod.id, name="target_table", value="marts_prod")
    await _seed_variable(session, workspace_id=ws_dev.id, name="target_table", value="marts_dev")

    cfg = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, value FROM raw"},
        "sink": {
            "connection": "dst",
            # Whole-string substitution — the worker swaps the literal
            # ``"${var.target_table}"`` for the variable's string value.
            "table": "${var.target_table}",
            "mode": "append",
        },
    }

    p_prod, pv_prod = await _seed_pipeline(session, workspace_id=ws_prod.id, name="p", config=cfg)
    p_dev, pv_dev = await _seed_pipeline(session, workspace_id=ws_dev.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws_prod.id, pipeline_id=p_prod.id, pipeline_version_id=pv_prod.id
    )
    await _seed_pending_run(
        session, workspace_id=ws_dev.id, pipeline_id=p_dev.id, pipeline_version_id=pv_dev.id
    )

    # Drain both — they're independent so claim order doesn't matter.
    await _run_pending(session, "mm1-prod")
    await _run_pending(session, "mm1-dev")

    # Data plane: each ws wrote to its own resolved table.
    def _rows(table: str) -> list[tuple]:
        c = sqlite3.connect(str(db_path))
        try:
            return list(c.execute(f"SELECT id, value FROM {table} ORDER BY id").fetchall())
        finally:
            c.close()

    assert _rows("marts_prod") == [(1, 100), (2, 200), (3, 300)]
    assert _rows("marts_dev") == [(1, 100), (2, 200), (3, 300)]

    # Catalog: ws_prod sees ``dst/marts_prod``; ws_dev sees ``dst/marts_dev``.
    repo = AssetRepository(session)
    prod_keys = {a.asset_key for a in await repo.list_for_workspace(workspace_id=ws_prod.id)}
    dev_keys = {a.asset_key for a in await repo.list_for_workspace(workspace_id=ws_dev.id)}
    assert "dst/marts_prod" in prod_keys
    assert "dst/marts_dev" not in prod_keys
    assert "dst/marts_dev" in dev_keys
    assert "dst/marts_prod" not in dev_keys
    # Source asset is the same physical table but each ws registers its
    # own catalog row (workspace_id is part of the asset PK).
    assert "src/raw" in prod_keys
    assert "src/raw" in dev_keys
