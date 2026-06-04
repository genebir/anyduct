"""Pipeline-evolution catalog over REST (Phase AGD).

PP proves (service level) that editing a pipeline's config between runs
leaves an append-only catalog: v1's output and v2's output both persist
as assets. This is the operator/analyst REST view — PATCH the pipeline to
point its sink at a new table, run again, and confirm GET /assets still
lists the v1 asset alongside the new v2 asset (catalog is immutable /
append-only; an edit never erases history).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from etlx_server.db.enums import RunStatus
from sqlalchemy.ext.asyncio import AsyncSession

from tests.db.test_onboarding_journey_scenario import (
    _build_app,
    _client,
    _drain_pending_runs,
    _seed_user_and_login,
)

pytestmark = pytest.mark.asyncio


async def test_agd_pipeline_evolution_catalog_over_rest(
    session: AsyncSession, tmp_path: Path
) -> None:
    db_path = tmp_path / "evo.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, amount INTEGER)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, 10), (2, 20)])
        conn.execute("CREATE TABLE stage_v1 (id INTEGER, amount INTEGER)")
        conn.execute("CREATE TABLE stage_v2 (id INTEGER, amount INTEGER)")
        conn.commit()
    finally:
        conn.close()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="evo@example.com")
        h = {"Authorization": f"Bearer {token}"}
        ws_id = (
            await client.post(
                "/workspaces",
                headers=h,
                json={"name": "Evo", "slug": "evo-ws", "color_hex": "#33AAFF"},
            )
        ).json()["id"]
        for name in ("src", "dst"):
            assert (
                await client.post(
                    f"/workspaces/{ws_id}/connections",
                    headers=h,
                    json={
                        "name": name,
                        "type": "sqlite",
                        "config": {"database": str(db_path)},
                        "secrets": {},
                    },
                )
            ).status_code == 201

        def _cfg(sink_table: str) -> dict:
            return {
                "source": {"connection": "src", "query": "SELECT id, amount FROM raw"},
                "sink": {"connection": "dst", "table": sink_table, "mode": "append"},
            }

        # ---- v1: sink → stage_v1, run ----
        pipe_id = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines",
                headers=h,
                json={"name": "evolving", "config": _cfg("stage_v1")},
            )
        ).json()["id"]
        await client.post(f"/workspaces/{ws_id}/pipelines/{pipe_id}/trigger", headers=h, json={})
        assert await _drain_pending_runs(session, "agd-1") == 1

        # ---- Operator edits the pipeline: sink → stage_v2 (new version) ----
        patch = await client.patch(
            f"/workspaces/{ws_id}/pipelines/{pipe_id}",
            headers=h,
            json={"config": _cfg("stage_v2")},
        )
        assert patch.status_code == 200, patch.text
        await client.post(f"/workspaces/{ws_id}/pipelines/{pipe_id}/trigger", headers=h, json={})
        assert await _drain_pending_runs(session, "agd-2") == 1

        # ---- Catalog kept BOTH outputs (append-only history) ----
        keys = {
            a["asset_key"]
            for a in (await client.get(f"/workspaces/{ws_id}/assets", headers=h)).json()
        }
        assert {"src/raw", "dst/stage_v1", "dst/stage_v2"} <= keys, keys

        # Two versions exist for the pipeline.
        versions = (
            await client.get(f"/workspaces/{ws_id}/pipelines/{pipe_id}/versions", headers=h)
        ).json()
        assert len(versions) >= 2, versions

        # Both runs succeeded.
        runs = (
            await client.get(f"/workspaces/{ws_id}/runs?pipeline_id={pipe_id}", headers=h)
        ).json()
        statuses = {r["status"] for r in runs}
        assert statuses == {RunStatus.SUCCEEDED.value}, runs

    # Each table physically got its run's rows.
    out = sqlite3.connect(str(db_path))
    try:
        assert out.execute("SELECT count(*) FROM stage_v1").fetchone()[0] == 2
        assert out.execute("SELECT count(*) FROM stage_v2").fetchone()[0] == 2
    finally:
        out.close()
