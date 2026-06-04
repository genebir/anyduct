"""Workspace-variable resolved lineage over REST (Phase AGC).

MM proves (service level) that ``${var.x}`` in a pipeline config resolves
before the catalog records lineage — so the asset key is the *resolved*
table name, not the literal ``${var.x}`` (a silent bug MM/ADR-0057 fixed).
This is the analyst's REST view: set a workspace variable via the API,
run a pipeline whose sink table is ``${var.target_table}``, and confirm
GET /assets shows the resolved key (so the analyst can actually find the
asset) — never the unresolved placeholder.
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


async def test_agc_var_resolves_in_catalog_over_rest(session: AsyncSession, tmp_path: Path) -> None:
    db_path = tmp_path / "var.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, value INTEGER)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, 10), (2, 20)])
        conn.execute("CREATE TABLE marts_prod (id INTEGER, value INTEGER)")
        conn.commit()
    finally:
        conn.close()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="var@example.com")
        h = {"Authorization": f"Bearer {token}"}
        ws_id = (
            await client.post(
                "/workspaces",
                headers=h,
                json={"name": "Var", "slug": "var-ws", "color_hex": "#33AAFF"},
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

        # Set the workspace variable over REST.
        vr = await client.put(
            f"/workspaces/{ws_id}/variables/target_table",
            headers=h,
            json={"value": "marts_prod", "description": "where marts land"},
        )
        assert vr.status_code == 200, vr.text

        pipe_id = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines",
                headers=h,
                json={
                    "name": "p",
                    "config": {
                        "source": {"connection": "src", "query": "SELECT id, value FROM raw"},
                        # The sink table is a variable reference — resolved at run time.
                        "sink": {
                            "connection": "dst",
                            "table": "${var.target_table}",
                            "mode": "append",
                        },
                    },
                },
            )
        ).json()["id"]

        run_id = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines/{pipe_id}/trigger", headers=h, json={}
            )
        ).json()["id"]
        assert await _drain_pending_runs(session, "agc") == 1
        run = (await client.get(f"/workspaces/{ws_id}/runs/{run_id}", headers=h)).json()
        assert run["status"] == RunStatus.SUCCEEDED.value

        # The catalog records the RESOLVED key, not the literal placeholder.
        keys = {
            a["asset_key"]
            for a in (await client.get(f"/workspaces/{ws_id}/assets", headers=h)).json()
        }
        assert "dst/marts_prod" in keys, keys
        assert not any("${var" in k for k in keys), keys

    # The data really landed in the resolved table.
    out = sqlite3.connect(str(db_path))
    try:
        assert out.execute("SELECT count(*) FROM marts_prod").fetchone()[0] == 2
    finally:
        out.close()
