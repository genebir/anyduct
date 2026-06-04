"""Analyst fan-out catalog journey over REST (Phase AFY).

EE Scenario B proves ``when``-routed fan-out lineage at the service level
(AssetRepository directly). This is the analyst's REST view of the same:
after a one-source → two-sink run, the catalog list + lineage endpoints
must show both sink assets, each with the source upstream — the topology
an analyst navigates in the web catalog. Complements KK (chain) with a
fan-out shape over HTTP.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from etlx_server.db.enums import RunStatus
from sqlalchemy.ext.asyncio import AsyncSession

from tests.db.test_backfill_scenarios import _query_rows
from tests.db.test_onboarding_journey_scenario import (
    _build_app,
    _client,
    _drain_pending_runs,
    _seed_user_and_login,
)

pytestmark = pytest.mark.asyncio


async def test_afy_fanout_catalog_over_rest(session: AsyncSession, tmp_path: Path) -> None:
    db_path = tmp_path / "fanout.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw_orders (id INTEGER, status TEXT, amount INTEGER)")
        conn.executemany(
            "INSERT INTO raw_orders VALUES (?, ?, ?)",
            [(10, "confirmed", 100), (11, "confirmed", 250), (12, "cancelled", 50)],
        )
        conn.execute("CREATE TABLE confirmed_orders (id INTEGER, status TEXT, amount INTEGER)")
        conn.execute("CREATE TABLE cancelled_orders (id INTEGER, status TEXT, amount INTEGER)")
        conn.commit()
    finally:
        conn.close()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="fanout@example.com")
        h = {"Authorization": f"Bearer {token}"}
        ws_id = (
            await client.post(
                "/workspaces",
                headers=h,
                json={"name": "FO", "slug": "fo-ws", "color_hex": "#33AAFF"},
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

        pipe_id = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines",
                headers=h,
                json={
                    "name": "fanout",
                    "config": {
                        "source": {
                            "connection": "src",
                            "query": "SELECT id, status, amount FROM raw_orders",
                        },
                        "sinks": [
                            {
                                "connection": "dst",
                                "table": "confirmed_orders",
                                "mode": "append",
                                "when": "data['status'] == 'confirmed'",
                            },
                            {
                                "connection": "dst",
                                "table": "cancelled_orders",
                                "mode": "append",
                                "when": "data['status'] == 'cancelled'",
                            },
                        ],
                    },
                },
            )
        ).json()["id"]

        run_id = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines/{pipe_id}/trigger", headers=h, json={}
            )
        ).json()["id"]
        assert await _drain_pending_runs(session, "afy") == 1
        run = (await client.get(f"/workspaces/{ws_id}/runs/{run_id}", headers=h)).json()
        assert run["status"] == RunStatus.SUCCEEDED.value

        # ---- Analyst opens the catalog: both sinks + source present ----
        assets = (await client.get(f"/workspaces/{ws_id}/assets", headers=h)).json()
        by_key = {a["asset_key"]: a for a in assets}
        for key in ("src/raw_orders", "dst/confirmed_orders", "dst/cancelled_orders"):
            assert key in by_key, by_key

        # ---- Each sink's lineage shows the shared source upstream ----
        for sink_key in ("dst/confirmed_orders", "dst/cancelled_orders"):
            lin = (
                await client.get(
                    f"/workspaces/{ws_id}/assets/{by_key[sink_key]['id']}/lineage",
                    headers=h,
                )
            ).json()
            ups = {u["asset_key"] for u in lin["upstream"]}
            assert "src/raw_orders" in ups, (sink_key, lin)

    # Records routed by predicate (data plane).
    assert _query_rows(db_path, "SELECT id FROM confirmed_orders ORDER BY id") == [(10,), (11,)]
    assert _query_rows(db_path, "SELECT id FROM cancelled_orders ORDER BY id") == [(12,)]
