"""Operator DLQ-investigation journey (Phase DLQ-1 over REST, ADR-0075).

User persona: an operator whose pipeline has a dead-letter queue. A run
finishes "succeeded" but quietly routed some bad records to the DLQ. They
want to open the run and actually *see* what failed — the question AFB's
count ("N routed to DLQ") raises but couldn't answer.

This walks the REST API the same order the UI does, so it dogfoods the
new ``GET /pipelines/{pid}/dlq/records`` endpoint through real auth +
router + response_model (the service-level test_dlq_preview_scenario
covers the logic; this proves the HTTP contract):

1. login → create workspace → register src + dst connections.
2. create a pipeline whose transform raises on bad rows, with ``dlq:``.
3. trigger → drain the worker.
4. ``GET /runs/{rid}`` → succeeded, only the good rows written.
5. ``GET /pipelines/{pid}/dlq/records`` → the bad rows, available=True.
6. a second pipeline with no DLQ → ``available=False, reason=no_dlq``.
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

_TRANSFORM = (
    "def transform(record):\n"
    "    if record.data['amount'] < 0:\n"
    "        raise ValueError('negative amount')\n"
    "    return record\n"
)


async def _make_ws_with_connections(client, h, db_path: Path) -> str:  # type: ignore[no-untyped-def]
    ws_resp = await client.post(
        "/workspaces",
        headers=h,
        json={"name": "DLQ WS", "slug": "dlq-ws", "color_hex": "#33AAFF"},
    )
    assert ws_resp.status_code == 201, ws_resp.text
    ws_id = ws_resp.json()["id"]
    for name in ("src", "dst"):
        c_resp = await client.post(
            f"/workspaces/{ws_id}/connections",
            headers=h,
            json={
                "name": name,
                "type": "sqlite",
                "config": {"database": str(db_path)},
                "secrets": {},
            },
        )
        assert c_resp.status_code == 201, c_resp.text
    return ws_id


async def test_dlqj_operator_views_dlq_records_over_rest(
    session: AsyncSession, tmp_path: Path
) -> None:
    # ---- The operator's warehouse: 3 good rows, 2 bad (negative amount) ----
    db_path = tmp_path / "dlqj.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw_orders (id INTEGER, amount INTEGER)")
        conn.executemany(
            "INSERT INTO raw_orders VALUES (?, ?)",
            [(1, 100), (2, -50), (3, 200), (4, -75), (5, 300)],
        )
        conn.execute("CREATE TABLE clean_orders (id INTEGER, amount INTEGER)")
        conn.execute("CREATE TABLE bad_orders (id INTEGER, amount INTEGER)")
        conn.commit()
    finally:
        conn.close()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="dlqop@example.com")
        h = {"Authorization": f"Bearer {token}"}
        ws_id = await _make_ws_with_connections(client, h, db_path)

        # ---- Pipeline with a DLQ ----------------------------------------
        pipe_resp = await client.post(
            f"/workspaces/{ws_id}/pipelines",
            headers=h,
            json={
                "name": "orders_with_dlq",
                "config": {
                    "source": {
                        "connection": "src",
                        "query": "SELECT id, amount FROM raw_orders",
                    },
                    "transforms": [{"type": "custom_python", "code": _TRANSFORM}],
                    "sink": {"connection": "dst", "table": "clean_orders", "mode": "append"},
                    "dlq": {"connection": "dst", "table": "bad_orders", "mode": "append"},
                },
            },
        )
        assert pipe_resp.status_code == 201, pipe_resp.text
        pipe_id = pipe_resp.json()["id"]

        # ---- Trigger + drain --------------------------------------------
        trig = await client.post(
            f"/workspaces/{ws_id}/pipelines/{pipe_id}/trigger", headers=h, json={}
        )
        assert trig.status_code == 202, trig.text
        run_id = trig.json()["id"]
        assert await _drain_pending_runs(session, "dlqj") == 1

        # ---- Run succeeded; only the good rows written ------------------
        run_resp = await client.get(f"/workspaces/{ws_id}/runs/{run_id}", headers=h)
        assert run_resp.status_code == 200, run_resp.text
        assert run_resp.json()["status"] == RunStatus.SUCCEEDED.value

        # ---- THE NEW HOP: view the DLQ records over REST ----------------
        dlq_resp = await client.get(
            f"/workspaces/{ws_id}/pipelines/{pipe_id}/dlq/records?limit=50", headers=h
        )
        assert dlq_resp.status_code == 200, dlq_resp.text
        dlq = dlq_resp.json()
        assert dlq["available"] is True
        assert dlq["connection"] == "dst"
        assert dlq["table"] == "bad_orders"
        by_id = sorted(dlq["records"], key=lambda r: r["id"])
        assert by_id == [{"id": 2, "amount": -50}, {"id": 4, "amount": -75}]

        # ---- A pipeline with no DLQ reports a clean reason, not an error -
        nodlq_resp = await client.post(
            f"/workspaces/{ws_id}/pipelines",
            headers=h,
            json={
                "name": "no_dlq",
                "config": {
                    "source": {"connection": "src", "query": "SELECT id FROM raw_orders"},
                    "sink": {"connection": "dst", "table": "clean_orders", "mode": "append"},
                },
            },
        )
        assert nodlq_resp.status_code == 201, nodlq_resp.text
        nodlq_id = nodlq_resp.json()["id"]
        nodlq_dlq = await client.get(
            f"/workspaces/{ws_id}/pipelines/{nodlq_id}/dlq/records", headers=h
        )
        assert nodlq_dlq.status_code == 200, nodlq_dlq.text
        body = nodlq_dlq.json()
        assert body["available"] is False
        assert body["reason"] == "no_dlq"
        assert body["records"] == []
