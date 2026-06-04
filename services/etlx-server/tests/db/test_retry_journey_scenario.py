"""Operator retry-after-failure journey over REST (Phase AFX).

The canonical "my run failed, I fixed the infra, I retry" flow, end to
end through the public API with real data — not a simulated status flip
(test_scenario_e2e) and not a unit on the repository.

login → workspace → connections → pipeline (sink table doesn't exist yet)
→ trigger → drain → GET run = FAILED (error_class set) → operator fixes
the warehouse (creates the missing table) → POST /runs/{rid}/retry →
drain → new run SUCCEEDED + rows written, and the ORIGINAL run row stays
FAILED with ``retry_of`` linking the new run back to it.
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


async def test_afx_retry_after_failure_full_journey(session: AsyncSession, tmp_path: Path) -> None:
    # Source has data + table; the sink TABLE is intentionally missing so
    # the first write fails (a stand-in for a transient infra problem).
    db_path = tmp_path / "retry.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw_orders (id INTEGER, amount INTEGER)")
        conn.executemany("INSERT INTO raw_orders VALUES (?, ?)", [(1, 100), (2, 200), (3, 300)])
        conn.commit()
    finally:
        conn.close()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="retry@example.com")
        h = {"Authorization": f"Bearer {token}"}
        ws_id = (
            await client.post(
                "/workspaces",
                headers=h,
                json={"name": "Retry", "slug": "retry-ws", "color_hex": "#33AAFF"},
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
                    "name": "load_orders",
                    "config": {
                        "source": {
                            "connection": "src",
                            "query": "SELECT id, amount FROM raw_orders",
                        },
                        # No auto_create_table → the missing sink table makes
                        # the first run fail at write time.
                        "sink": {"connection": "dst", "table": "clean_orders", "mode": "append"},
                    },
                },
            )
        ).json()["id"]

        # ---- First attempt fails (sink table missing) ----
        run1_id = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines/{pipe_id}/trigger", headers=h, json={}
            )
        ).json()["id"]
        assert await _drain_pending_runs(session, "afx-1") == 1
        run1 = (await client.get(f"/workspaces/{ws_id}/runs/{run1_id}", headers=h)).json()
        assert run1["status"] == RunStatus.FAILED.value
        assert run1["error_class"], run1  # the UI shows this (AEV/AEW)

        # ---- Operator fixes the warehouse (creates the missing table) ----
        fix = sqlite3.connect(str(db_path))
        try:
            fix.execute("CREATE TABLE clean_orders (id INTEGER, amount INTEGER)")
            fix.commit()
        finally:
            fix.close()

        # ---- Retry: new pending row, links back via retry_of ----
        retry_resp = await client.post(f"/workspaces/{ws_id}/runs/{run1_id}/retry", headers=h)
        assert retry_resp.status_code in (200, 202), retry_resp.text
        run2_id = retry_resp.json()["id"]
        assert run2_id != run1_id

        assert await _drain_pending_runs(session, "afx-2") == 1
        run2 = (await client.get(f"/workspaces/{ws_id}/runs/{run2_id}", headers=h)).json()
        assert run2["status"] == RunStatus.SUCCEEDED.value
        assert run2["records_written"] == 3
        # retry_of links the new run back to the original (RunDetail only).
        assert run2["result_json"]["retry_of"] == run1_id

        # ---- The original run is untouched (still FAILED) ----
        run1_again = (await client.get(f"/workspaces/{ws_id}/runs/{run1_id}", headers=h)).json()
        assert run1_again["status"] == RunStatus.FAILED.value

    # The retry actually wrote the data.
    assert _query_rows(db_path, "SELECT id FROM clean_orders ORDER BY id") == [
        (1,),
        (2,),
        (3,),
    ]
