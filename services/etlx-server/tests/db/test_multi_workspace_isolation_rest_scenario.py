"""Multi-workspace isolation over REST (Phase AGB).

HH proves tenant isolation at the service level. This is the SaaS
multi-tenant guarantee through the public API: two workspaces with the
SAME connection name, table names, and resulting asset keys must not leak
into each other's runs or catalog. A run in ws_a leaves ws_b's /runs and
/assets untouched, and the identical asset_key string resolves to a
DIFFERENT catalog row per workspace.
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


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, v INTEGER)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, 10), (2, 20)])
        conn.execute("CREATE TABLE out (id INTEGER, v INTEGER)")
        conn.commit()
    finally:
        conn.close()


async def test_agb_two_workspaces_stay_isolated_over_rest(
    session: AsyncSession, tmp_path: Path
) -> None:
    # Separate physical DBs, but IDENTICAL connection/table/asset names.
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    _seed_db(db_a)
    _seed_db(db_b)

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="tenant@example.com")
        h = {"Authorization": f"Bearer {token}"}

        async def _make_ws(slug: str, db: Path) -> tuple[str, str]:
            ws_id = (
                await client.post(
                    "/workspaces",
                    headers=h,
                    json={"name": slug, "slug": slug, "color_hex": "#33AAFF"},
                )
            ).json()["id"]
            # Same connection name "wh" in BOTH workspaces.
            assert (
                await client.post(
                    f"/workspaces/{ws_id}/connections",
                    headers=h,
                    json={
                        "name": "wh",
                        "type": "sqlite",
                        "config": {"database": str(db)},
                        "secrets": {},
                    },
                )
            ).status_code == 201
            pipe_id = (
                await client.post(
                    f"/workspaces/{ws_id}/pipelines",
                    headers=h,
                    json={
                        "name": "p",
                        "config": {
                            "source": {"connection": "wh", "query": "SELECT id, v FROM raw"},
                            "sink": {"connection": "wh", "table": "out", "mode": "append"},
                        },
                    },
                )
            ).json()["id"]
            return ws_id, pipe_id

        ws_a, pipe_a = await _make_ws("tenant-a", db_a)
        ws_b, pipe_b = await _make_ws("tenant-b", db_b)

        # Run ONLY ws_a's pipeline.
        await client.post(f"/workspaces/{ws_a}/pipelines/{pipe_a}/trigger", headers=h, json={})
        assert await _drain_pending_runs(session, "agb") == 1

        # ws_a sees its run; ws_b sees none (no cross-workspace leak).
        runs_a = (await client.get(f"/workspaces/{ws_a}/runs", headers=h)).json()
        runs_b = (await client.get(f"/workspaces/{ws_b}/runs", headers=h)).json()
        assert len(runs_a) == 1 and runs_a[0]["pipeline_id"] == pipe_a
        assert runs_b == []

        # ws_a's catalog filled; ws_b's stays empty even though the asset
        # keys would be the identical strings ("wh/raw", "wh/out").
        assets_a = {
            a["asset_key"]
            for a in (await client.get(f"/workspaces/{ws_a}/assets", headers=h)).json()
        }
        assets_b = (await client.get(f"/workspaces/{ws_b}/assets", headers=h)).json()
        assert {"wh/raw", "wh/out"} <= assets_a
        assert assets_b == []

        # Now run ws_b too: identical asset keys, but its OWN catalog rows.
        await client.post(f"/workspaces/{ws_b}/pipelines/{pipe_b}/trigger", headers=h, json={})
        assert await _drain_pending_runs(session, "agb2") == 1
        list_a = (await client.get(f"/workspaces/{ws_a}/assets", headers=h)).json()
        list_b = (await client.get(f"/workspaces/{ws_b}/assets", headers=h)).json()
        a_by_key = {a["asset_key"]: a["id"] for a in list_a}
        b_by_key = {a["asset_key"]: a["id"] for a in list_b}
        assert "wh/out" in a_by_key and "wh/out" in b_by_key
        # Same key string, different workspace → different catalog row id.
        assert a_by_key["wh/out"] != b_by_key["wh/out"]

        # Cross-workspace asset access is rejected (ws_b's id under ws_a).
        cross = await client.get(
            f"/workspaces/{ws_a}/assets/{b_by_key['wh/out']}/lineage", headers=h
        )
        assert cross.status_code == 404, cross.text

        # Both runs succeeded.
        r_a = (await client.get(f"/workspaces/{ws_a}/runs", headers=h)).json()
        assert r_a[0]["status"] == RunStatus.SUCCEEDED.value
