"""Operator backfill journey over REST (Phase AFW, ADR-0039).

The service-level JJ scenarios (test_backfill_scenarios) seed the backfill
run directly. This walks the *full* operator journey through the public
API — the "reprocess just this window" flow — and verifies that only the
cursor-windowed rows actually land in the sink after the worker runs:

login → workspace → src+dst connections → pipeline (cursor_column) →
POST /pipelines/{id}/backfill {cursor_from, cursor_to} → drain worker →
GET run (succeeded, backfill marker) → only ``(from, to]`` rows in sink.

Complements test_run_actions_router (enqueue shape) + JJ (worker logic):
this is the end-to-end HTTP→worker→data contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from etlx_server.db.enums import RunStatus
from sqlalchemy.ext.asyncio import AsyncSession

from tests.db.test_backfill_scenarios import _query_rows, _seed_ten_day_warehouse
from tests.db.test_onboarding_journey_scenario import (
    _build_app,
    _client,
    _drain_pending_runs,
    _seed_user_and_login,
)

pytestmark = pytest.mark.asyncio


async def test_afw_operator_backfills_a_window_over_rest(
    session: AsyncSession, tmp_path: Path
) -> None:
    db_path = _seed_ten_day_warehouse(tmp_path)  # 10 rows, days 2026-05-01..10

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="bf@example.com")
        h = {"Authorization": f"Bearer {token}"}

        ws_id = (
            await client.post(
                "/workspaces",
                headers=h,
                json={"name": "BF", "slug": "bf-ws", "color_hex": "#33AAFF"},
            )
        ).json()["id"]
        for name in ("src", "dst"):
            c = await client.post(
                f"/workspaces/{ws_id}/connections",
                headers=h,
                json={
                    "name": name,
                    "type": "sqlite",
                    "config": {"database": str(db_path)},
                    "secrets": {},
                },
            )
            assert c.status_code == 201, c.text

        pipe = await client.post(
            f"/workspaces/{ws_id}/pipelines",
            headers=h,
            json={
                "name": "replicate",
                "config": {
                    "source": {
                        "connection": "src",
                        "query": "SELECT id, created_at, value FROM raw_events",
                        "cursor_column": "created_at",
                    },
                    "sink": {
                        "connection": "dst",
                        "table": "replicated_events",
                        "mode": "append",
                    },
                },
            },
        )
        assert pipe.status_code == 201, pipe.text
        pipe_id = pipe.json()["id"]

        # ---- The new hop: backfill a 3-day window over REST ----
        bf = await client.post(
            f"/workspaces/{ws_id}/pipelines/{pipe_id}/backfill",
            headers=h,
            json={"cursor_from": "2026-05-02", "cursor_to": "2026-05-05"},
        )
        assert bf.status_code == 202, bf.text
        run_id = bf.json()["id"]

        assert await _drain_pending_runs(session, "afw") == 1

        run = await client.get(f"/workspaces/{ws_id}/runs/{run_id}", headers=h)
        assert run.status_code == 200, run.text
        rbody = run.json()
        assert rbody["status"] == RunStatus.SUCCEEDED.value
        # The backfill marker survives for audit ("this run was a backfill").
        assert rbody["result_json"]["backfill"] == {
            "cursor_from": "2026-05-02",
            "cursor_to": "2026-05-05",
        }

    # Only days 3, 4, 5 — exclusive lower, inclusive upper (ADR-0039). The
    # other 7 rows must NOT be in the sink: this proves the window applied
    # rather than a full read.
    landed = _query_rows(db_path, "SELECT id FROM replicated_events ORDER BY id")
    assert landed == [(3,), (4,), (5,)]
