"""Auto-materialize chain over REST (Phase AGA).

GG proves event-driven auto-materialize at the service level (seeds the
pending run directly). This is the asset-driven-orchestration vision
through the public API: an operator triggers pipeline A over HTTP, and B
(``auto_materialize: true``, reading A's output asset) runs on its own
when the worker drains — no second manual trigger. Verifies the chain ran
and the catalog reflects raw → staging → mart over REST.
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


async def test_aga_auto_materialize_chain_over_rest(session: AsyncSession, tmp_path: Path) -> None:
    db_path = tmp_path / "chain.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, amount INTEGER)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, 100), (2, 250), (3, 50)])
        conn.execute("CREATE TABLE staging (id INTEGER, amount INTEGER)")
        conn.execute("CREATE TABLE mart (id INTEGER, double_amount INTEGER)")
        conn.commit()
    finally:
        conn.close()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="am@example.com")
        h = {"Authorization": f"Bearer {token}"}
        ws_id = (
            await client.post(
                "/workspaces",
                headers=h,
                json={"name": "AM", "slug": "am-ws", "color_hex": "#33AAFF"},
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

        a_id = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines",
                headers=h,
                json={
                    "name": "A_stage",
                    "config": {
                        "source": {"connection": "src", "query": "SELECT id, amount FROM raw"},
                        "sink": {"connection": "dst", "table": "staging", "mode": "append"},
                    },
                },
            )
        ).json()["id"]
        # B auto-materializes off A's output asset (dst/staging).
        b_id = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines",
                headers=h,
                json={
                    "name": "B_mart",
                    "config": {
                        "auto_materialize": True,
                        "source": {
                            "connection": "dst",
                            "query": "SELECT id, amount * 2 AS double_amount FROM staging",
                        },
                        "sink": {"connection": "dst", "table": "mart", "mode": "append"},
                    },
                },
            )
        ).json()["id"]

        # Trigger ONLY A. The auto-trigger should enqueue B; draining runs both.
        await client.post(f"/workspaces/{ws_id}/pipelines/{a_id}/trigger", headers=h, json={})
        assert await _drain_pending_runs(session, "aga") == 2

        # Both pipelines have exactly one succeeded run (B was auto-enqueued).
        runs = (await client.get(f"/workspaces/{ws_id}/runs", headers=h)).json()
        by_pipe: dict[str, list] = {}
        for r in runs:
            by_pipe.setdefault(r["pipeline_id"], []).append(r)
        assert len(by_pipe.get(a_id, [])) == 1
        assert len(by_pipe.get(b_id, [])) == 1
        assert by_pipe[a_id][0]["status"] == RunStatus.SUCCEEDED.value
        assert by_pipe[b_id][0]["status"] == RunStatus.SUCCEEDED.value

        # Catalog shows the full chain over REST.
        assets = (await client.get(f"/workspaces/{ws_id}/assets", headers=h)).json()
        keys = {a["asset_key"] for a in assets}
        assert {"src/raw", "dst/staging", "dst/mart"} <= keys, keys

    # B actually consumed staging and wrote mart.
    assert _query_rows(db_path, "SELECT id, double_amount FROM mart ORDER BY id") == [
        (1, 200),
        (2, 500),
        (3, 100),
    ]
