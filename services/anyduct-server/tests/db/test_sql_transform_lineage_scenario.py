"""Analyst journey — sql dataset transform column lineage (ADR-0093 f/u).

The ``sql`` transform's body is SQL, so the catalog infers per-column
lineage through it automatically (the Phase X sqlglot walker over the
in-flight view). This locks the full path over REST: pipeline with a
GROUP BY sql transform → trigger → drain → the sink asset's
``/column-lineage`` shows ``region ← sales.region`` and
``total ← sales.amount`` with ``opaque=False`` — no manual
``column_mapping`` declared anywhere.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.db.test_onboarding_journey_scenario import (
    _build_app,
    _client,
    _drain_pending_runs,
    _seed_user_and_login,
)

pytestmark = pytest.mark.asyncio


async def test_sql_transform_column_lineage_inferred_over_rest(
    session: AsyncSession, tmp_path: Path
) -> None:
    db_path = tmp_path / "sqlt.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE sales (region TEXT, amount INTEGER)")
        conn.executemany(
            "INSERT INTO sales VALUES (?, ?)",
            [("EU", 10), ("US", 5), ("EU", 7)],
        )
        conn.execute("CREATE TABLE region_totals (region TEXT, total INTEGER)")
        conn.commit()
    finally:
        conn.close()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="sqlt@example.com")
        h = {"Authorization": f"Bearer {token}"}
        ws_id = (
            await client.post(
                "/workspaces",
                headers=h,
                json={"name": "SQLT", "slug": "sqlt-ws", "color_hex": "#33AAFF"},
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
                "name": "rollup",
                "config": {
                    "source": {
                        "connection": "src",
                        "query": "SELECT region, amount FROM sales",
                    },
                    "transforms": [
                        {
                            "type": "sql",
                            "query": (
                                "SELECT region, SUM(amount) AS total FROM input GROUP BY region"
                            ),
                        }
                    ],
                    "sink": {
                        "connection": "dst",
                        "table": "region_totals",
                        "mode": "append",
                    },
                },
            },
        )
        assert pipe.status_code == 201, pipe.text
        pipe_id = pipe.json()["id"]

        trig = await client.post(
            f"/workspaces/{ws_id}/pipelines/{pipe_id}/trigger", headers=h, json={}
        )
        assert trig.status_code == 202, trig.text
        assert await _drain_pending_runs(session, "sqlt") == 1

        assets = (await client.get(f"/workspaces/{ws_id}/assets", headers=h)).json()
        by_key = {a["asset_key"]: a for a in assets}
        dst_asset = by_key["dst/region_totals"]
        # List-level flag: the sql transform did NOT make the asset opaque.
        assert dst_asset["column_lineage_opaque"] is False

        resp = await client.get(
            f"/workspaces/{ws_id}/assets/{dst_asset['id']}/column-lineage", headers=h
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["opaque"] is False
        by_name = {c["name"]: c for c in body["columns"]}
        assert [(u["asset_key"], u["column"]) for u in by_name["region"]["upstreams"]] == [
            ("src/sales", "region")
        ]
        # The aggregate traces through the in-flight view to its source
        # column — the inference the sqlglot walker provides for free.
        assert [(u["asset_key"], u["column"]) for u in by_name["total"]["upstreams"]] == [
            ("src/sales", "amount")
        ]


async def test_multi_source_join_column_lineage_over_rest(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The P1c shape end-to-end: two sources → graph join → sql aggregate
    → sink. Column lineage traces each output to the RIGHT source table
    (region ← customers, total ← orders) — the graph topological walk
    (join=union, sql=sqlglot) shipped 2026-06-12; v1 marked this opaque."""
    db_path = tmp_path / "msj.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE orders (customer_id INTEGER, amount INTEGER)")
        conn.executemany("INSERT INTO orders VALUES (?, ?)", [(1, 10), (2, 5), (1, 7)])
        conn.execute("CREATE TABLE customers (customer_id INTEGER, region TEXT)")
        conn.executemany("INSERT INTO customers VALUES (?, ?)", [(1, "EU"), (2, "US")])
        conn.execute("CREATE TABLE region_totals (region TEXT, total INTEGER)")
        conn.commit()
    finally:
        conn.close()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="msj@example.com")
        h = {"Authorization": f"Bearer {token}"}
        ws_id = (
            await client.post(
                "/workspaces",
                headers=h,
                json={"name": "MSJ", "slug": "msj-ws", "color_hex": "#33AAFF"},
            )
        ).json()["id"]
        for name in ("a", "b"):
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
                "name": "join-rollup",
                "config": {
                    "graph": {
                        "nodes": [
                            {
                                "id": "o",
                                "type": "source",
                                "connection": "a",
                                "query": "SELECT customer_id, amount FROM orders",
                            },
                            {
                                "id": "c",
                                "type": "source",
                                "connection": "b",
                                "query": "SELECT customer_id, region FROM customers",
                            },
                            {"id": "j", "type": "join", "on": ["customer_id"]},
                            {
                                "id": "x",
                                "type": "transform",
                                "transform": {
                                    "type": "sql",
                                    "query": (
                                        "SELECT region, SUM(amount) AS total "
                                        "FROM input GROUP BY region"
                                    ),
                                },
                            },
                            {
                                "id": "k",
                                "type": "sink",
                                "connection": "a",
                                "table": "region_totals",
                            },
                        ],
                        "edges": [
                            {"from_node": "o", "to_node": "j"},
                            {"from_node": "c", "to_node": "j"},
                            {"from_node": "j", "to_node": "x"},
                            {"from_node": "x", "to_node": "k"},
                        ],
                    }
                },
            },
        )
        assert pipe.status_code == 201, pipe.text
        pipe_id = pipe.json()["id"]
        trig = await client.post(
            f"/workspaces/{ws_id}/pipelines/{pipe_id}/trigger", headers=h, json={}
        )
        assert trig.status_code == 202, trig.text
        assert await _drain_pending_runs(session, "msj") == 1

        assets = (await client.get(f"/workspaces/{ws_id}/assets", headers=h)).json()
        dst = {a["asset_key"]: a for a in assets}["a/region_totals"]
        assert dst["column_lineage_opaque"] is False
        body = (
            await client.get(f"/workspaces/{ws_id}/assets/{dst['id']}/column-lineage", headers=h)
        ).json()
        by_name = {c["name"]: c for c in body["columns"]}
        assert [(u["asset_key"], u["column"]) for u in by_name["region"]["upstreams"]] == [
            ("b/customers", "region")
        ]
        assert [(u["asset_key"], u["column"]) for u in by_name["total"]["upstreams"]] == [
            ("a/orders", "amount")
        ]
