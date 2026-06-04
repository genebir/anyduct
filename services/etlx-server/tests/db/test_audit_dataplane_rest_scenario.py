"""Compliance data-plane audit over REST (Phase AFZ).

QQ (test_audit_trail_scenarios) proves the worker records data-plane audit
events (run.sql_read / run.python_executed / run.sql_executed), but checks
them via the repository. The onboarding journey covers only run.sql_read
over REST. This walks the compliance officer's REST query for ALL three:
run a pipeline with a custom_python transform AND a sql_exec, then
``GET /audit?action=...`` and confirm each event is queryable with its
payload — the "what code/SQL ran in this run?" answer the UI relies on.
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


async def test_afz_data_plane_audit_events_queryable_over_rest(
    session: AsyncSession, tmp_path: Path
) -> None:
    db_path = tmp_path / "audit.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, name TEXT)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, "a"), (2, "b")])
        conn.execute("CREATE TABLE out (id INTEGER, name TEXT)")
        conn.commit()
    finally:
        conn.close()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="audit@example.com")
        h = {"Authorization": f"Bearer {token}"}
        ws_id = (
            await client.post(
                "/workspaces",
                headers=h,
                json={"name": "Aud", "slug": "aud-ws", "color_hex": "#33AAFF"},
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
                    "name": "audited",
                    "config": {
                        "source": {"connection": "src", "query": "SELECT id, name FROM raw"},
                        "transforms": [
                            {
                                "type": "custom_python",
                                "code": "def transform(record):\n    return record\n",
                            },
                            {
                                "type": "sql_exec",
                                "connection": "dst",
                                "statement": "SELECT 1",
                            },
                        ],
                        "sink": {"connection": "dst", "table": "out", "mode": "append"},
                    },
                },
            )
        ).json()["id"]

        run_id = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines/{pipe_id}/trigger", headers=h, json={}
            )
        ).json()["id"]
        assert await _drain_pending_runs(session, "afz") == 1
        run = (await client.get(f"/workspaces/{ws_id}/runs/{run_id}", headers=h)).json()
        assert run["status"] == RunStatus.SUCCEEDED.value

        async def _audit(action: str) -> list[dict]:
            resp = await client.get(f"/audit?workspace_id={ws_id}&action={action}", headers=h)
            assert resp.status_code == 200, resp.text
            return [r for r in resp.json() if r["resource_id"] == run_id]

        # ---- run.sql_read (source SELECT) ----
        reads = await _audit("run.sql_read")
        assert len(reads) == 1, reads
        assert reads[0]["after_json"]["query"] == "SELECT id, name FROM raw"

        # ---- run.python_executed (custom_python body) ----
        pys = await _audit("run.python_executed")
        assert len(pys) == 1, pys
        assert pys[0]["after_json"]["kind"] == "transform:custom_python"
        assert "code_hash" in pys[0]["after_json"]

        # ---- run.sql_executed (the sql_exec statement) ----
        execs = await _audit("run.sql_executed")
        assert len(execs) == 1, execs
        assert execs[0]["after_json"] is not None
