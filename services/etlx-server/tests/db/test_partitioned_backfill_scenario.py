"""Partitioned backfill — single-run scale-out over REST (ADR-0095).

One large cursor range splits into N independent sub-runs (one per
consecutive boundary pair, half-open windows) that the SKIP LOCKED queue
spreads across worker replicas. This walks the operator journey:

login → workspace → connections → pipeline (cursor_column) →
POST /pipelines/{id}/partitioned-backfill {boundaries} → N runs enqueued
→ drain worker → every window landed, no overlap, union complete →
``result_json.partition`` ties the group together.

Validation (422/400) is covered alongside: too few boundaries,
non-increasing boundaries, mixed types, and a pipeline with no
``cursor_column``.
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


async def _setup_pipeline(client, h: dict, db_path: str, *, cursor: bool = True) -> tuple[str, str]:
    """Workspace + src/dst connections + cursor pipeline; returns (ws, pid)."""
    ws_id = (
        await client.post(
            "/workspaces",
            headers=h,
            json={"name": "PB", "slug": "pb-ws", "color_hex": "#33AAFF"},
        )
    ).json()["id"]
    for name in ("src", "dst"):
        c = await client.post(
            f"/workspaces/{ws_id}/connections",
            headers=h,
            json={
                "name": name,
                "type": "sqlite",
                "config": {"database": db_path},
                "secrets": {},
            },
        )
        assert c.status_code == 201, c.text
    source: dict = {
        "connection": "src",
        "query": "SELECT id, created_at, value FROM raw_events",
    }
    if cursor:
        source["cursor_column"] = "created_at"
    pipe = await client.post(
        f"/workspaces/{ws_id}/pipelines",
        headers=h,
        json={
            "name": "replicate",
            "config": {
                "source": source,
                "sink": {"connection": "dst", "table": "replicated_events", "mode": "append"},
            },
        },
    )
    assert pipe.status_code == 201, pipe.text
    return ws_id, pipe.json()["id"]


async def test_partitioned_backfill_covers_range_without_overlap(
    session: AsyncSession, tmp_path: Path
) -> None:
    db_path = _seed_ten_day_warehouse(tmp_path)  # days 2026-05-01..10

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="pb@example.com")
        h = {"Authorization": f"Bearer {token}"}
        ws_id, pipe_id = await _setup_pipeline(client, h, str(db_path))

        # 3 windows: (04-30, 05-04] / (05-04, 05-07] / (05-07, 05-10]
        # → days 1-4, 5-7, 8-10. Union = all ten rows, intersection = ∅.
        resp = await client.post(
            f"/workspaces/{ws_id}/pipelines/{pipe_id}/partitioned-backfill",
            headers=h,
            json={"boundaries": ["2026-04-30", "2026-05-04", "2026-05-07", "2026-05-10"]},
        )
        assert resp.status_code == 202, resp.text
        summaries = resp.json()
        assert len(summaries) == 3

        # All three sub-runs drain (multi-replica workers would claim these
        # concurrently — P3a proved the queue's no-double-claim property).
        assert await _drain_pending_runs(session, "pb") == 3

        groups = set()
        indexes = []
        for s in summaries:
            run = await client.get(f"/workspaces/{ws_id}/runs/{s['id']}", headers=h)
            assert run.status_code == 200, run.text
            rbody = run.json()
            assert rbody["status"] == RunStatus.SUCCEEDED.value
            part = rbody["result_json"]["partition"]
            groups.add(part["group"])
            indexes.append(part["index"])
            assert part["of"] == 3
            assert "cursor_from" in rbody["result_json"]["backfill"]
        assert len(groups) == 1  # one group id ties the sub-runs together
        assert sorted(indexes) == [0, 1, 2]

    # Every source row landed exactly once — windows are half-open, so
    # coverage is complete AND overlap-free (the scale-out correctness core).
    landed = _query_rows(db_path, "SELECT id FROM replicated_events ORDER BY id")
    assert landed == [(i,) for i in range(1, 11)]


async def test_partitioned_backfill_validation(session: AsyncSession, tmp_path: Path) -> None:
    db_path = _seed_ten_day_warehouse(tmp_path)

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="pb2@example.com")
        h = {"Authorization": f"Bearer {token}"}
        ws_id, pipe_id = await _setup_pipeline(client, h, str(db_path))
        url = f"/workspaces/{ws_id}/pipelines/{pipe_id}/partitioned-backfill"

        # A single boundary defines no window.
        assert (await client.post(url, headers=h, json={"boundaries": ["a"]})).status_code == 422
        # Non-increasing boundaries would create an empty/negative window.
        r = await client.post(url, headers=h, json={"boundaries": ["b", "a"]})
        assert r.status_code == 422
        # Mixed types can't be compared meaningfully by the cursor SQL.
        r = await client.post(url, headers=h, json={"boundaries": [1, "b"]})
        assert r.status_code == 422

    # No runs were enqueued by any rejected request.
    landed = _query_rows(db_path, "SELECT COUNT(*) FROM replicated_events")
    assert landed == [(0,)]


async def test_partitioned_backfill_requires_cursor_column(
    session: AsyncSession, tmp_path: Path
) -> None:
    db_path = _seed_ten_day_warehouse(tmp_path)

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="pb3@example.com")
        h = {"Authorization": f"Bearer {token}"}
        ws_id, pipe_id = await _setup_pipeline(client, h, str(db_path), cursor=False)

        r = await client.post(
            f"/workspaces/{ws_id}/pipelines/{pipe_id}/partitioned-backfill",
            headers=h,
            json={"boundaries": [0, 5]},
        )
        assert r.status_code == 400
        assert "cursor_column" in r.json()["detail"]
