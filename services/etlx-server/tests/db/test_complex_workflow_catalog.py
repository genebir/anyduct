"""End-to-end "dogfooding" catalog scenario (Phase BB, 2026-05-29).

A 4-stage e-commerce pipeline that exercises every shape we promised the
catalog would auto-derive:

* **Stage 1** — multi-source SQL JOIN + CTE
  (``raw_orders`` + ``raw_customers`` → ``staging_orders_enriched``).
* **Stage 2** — CTE + ``ROW_NUMBER()`` window
  (``staging_orders_enriched`` → ``mart_top_orders``).
* **Stage 3** — ``custom_python`` transform that the static analyser can't
  trace, recovered via schema-passthrough fallback
  (``mart_top_orders`` → ``report_customer_tier``, plus a new ``tier``
  column the python code injected that legitimately has no upstream).
* **Stage 4** — ``SELECT *`` resolved at run time via SchemaInspector
  (``raw_products`` → ``products_clone``).

After running the four pipelines back-to-back, the test inspects the
catalog and asserts:

* every base table is registered (raw + staging + mart + report + clone);
* the asset edges form the expected DAG;
* the column lineage rows are correct on every sink (per-column + the
  passthrough ``tier`` row has empty upstreams + no asset is marked
  ``column_lineage_opaque``).

If any new SQL shape regresses, this scenario flags it as a single
holistic failure — much more useful than chasing isolated unit-test
regressions.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from etlx_server.assets.repository import AssetRepository
from etlx_server.worker.claim import claim_pending_run
from etlx_server.worker.executor import RunExecutor
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import StaticSecretBackend

# Reuse the helpers from the worker lifecycle suite — they already match
# the conftest's outer-transaction fixture pattern.
from tests.db.test_worker_lifecycle import (
    _seed_connection,
    _seed_pending_run,
    _seed_pipeline,
    _seed_workspace,
    _SessionFactoryAdapter,
)

pytestmark = pytest.mark.asyncio


def _seed_ecommerce_warehouse(tmp_path: Path) -> Path:
    """Create the on-disk sqlite warehouse: three raw tables + four empty
    sinks (the workers don't auto-create tables). Real driver round-trips
    so the catalog rows come from genuine reads + writes.
    """
    db = tmp_path / "ecom.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE raw_customers (id INTEGER, email TEXT, country TEXT, tier TEXT)")
        conn.executemany(
            "INSERT INTO raw_customers (id, email, country, tier) VALUES (?, ?, ?, ?)",
            [
                (1, "alice@ex.com", "KR", "gold"),
                (2, "bob@ex.com", "US", "silver"),
                (3, "carol@ex.com", "JP", "bronze"),
            ],
        )
        conn.execute(
            "CREATE TABLE raw_orders ("
            "id INTEGER, customer_id INTEGER, amount INTEGER, status TEXT, created_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO raw_orders (id, customer_id, amount, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (10, 1, 150, "confirmed", "2026-05-01"),
                (11, 1, 80, "confirmed", "2026-05-02"),
                (12, 2, 220, "confirmed", "2026-05-03"),
                (13, 3, 30, "cancelled", "2026-05-04"),
                (14, 2, 60, "confirmed", "2026-05-05"),
            ],
        )
        conn.execute(
            "CREATE TABLE raw_products (id INTEGER, name TEXT, category TEXT, price INTEGER)"
        )
        conn.executemany(
            "INSERT INTO raw_products (id, name, category, price) VALUES (?, ?, ?, ?)",
            [
                (101, "widget", "tools", 25),
                (102, "gadget", "tools", 75),
                (103, "gizmo", "toys", 45),
            ],
        )

        # Empty sinks — the sqlite sink requires the table to exist already.
        conn.execute(
            "CREATE TABLE staging_orders_enriched ("
            "id INTEGER, customer_id INTEGER, email TEXT, country TEXT, amount INTEGER)"
        )
        conn.execute("CREATE TABLE mart_top_orders (customer_id INTEGER, top_amount INTEGER)")
        conn.execute(
            "CREATE TABLE report_customer_tier (customer_id INTEGER, top_amount INTEGER, tier TEXT)"
        )
        conn.execute(
            "CREATE TABLE products_clone (id INTEGER, name TEXT, category TEXT, price INTEGER)"
        )
        conn.commit()
    finally:
        conn.close()
    return db


async def test_complex_ecommerce_workflow_full_catalog(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Run the four pipelines and assert the catalog reflects every shape.

    The pipelines share the same sqlite file (``src`` and ``dst`` are two
    connections pointing at the same database, the way a real warehouse
    often runs read + write against the same engine). Asset keys are
    ``connection/table`` so ``src/staging_orders_enriched`` and
    ``dst/staging_orders_enriched`` are intentionally distinct rows —
    that's the same shape ``derive_asset_key`` gives in any cross-connection
    chain.
    """
    db_path = _seed_ecommerce_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug="ecom-flow")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    # ---- Stage 1: SQL JOIN + CTE → staging_orders_enriched -----------
    p1_cfg = {
        "name": "stage1_enrich",
        "source": {
            "connection": "src",
            "query": (
                "WITH valid_orders AS ("
                "  SELECT id, customer_id, amount FROM raw_orders WHERE status = 'confirmed'"
                ") "
                "SELECT o.id, o.customer_id, c.email, c.country, o.amount "
                "FROM valid_orders o JOIN raw_customers c ON c.id = o.customer_id"
            ),
        },
        "sink": {"connection": "dst", "table": "staging_orders_enriched", "mode": "append"},
    }

    # ---- Stage 2: CTE + ROW_NUMBER window → mart_top_orders ----------
    p2_cfg = {
        "name": "stage2_topn",
        "source": {
            "connection": "src",
            "query": (
                "WITH ranked AS ("
                "  SELECT customer_id, amount, "
                "         ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY amount DESC) AS rk "
                "  FROM staging_orders_enriched"
                ") "
                "SELECT customer_id, amount AS top_amount FROM ranked WHERE rk = 1"
            ),
        },
        "sink": {"connection": "dst", "table": "mart_top_orders", "mode": "append"},
    }

    # ---- Stage 3: custom_python → report_customer_tier ---------------
    # The python code injects ``tier`` based on ``top_amount``. Static
    # analyser doesn't read python, so column lineage initially marks
    # this opaque — the schema-passthrough fallback (ADR-0046) recovers
    # ``customer_id`` + ``top_amount`` by name match. ``tier`` legitimately
    # has no upstream (the code created it).
    p3_cfg = {
        "name": "stage3_tier",
        "source": {
            "connection": "src",
            "query": "SELECT customer_id, top_amount FROM mart_top_orders",
        },
        "transforms": [
            {
                "type": "custom_python",
                # ``custom_python`` receives a ``Record`` instance (data +
                # metadata + schema_version). Mutating in place isn't
                # allowed; rebuild the record with the new column. This is
                # exactly the documented contract in
                # ``etl_plugins.runtime.transforms._build_custom_python``.
                "code": (
                    "def transform(record):\n"
                    "    d = dict(record.data)\n"
                    "    d['tier'] = 'gold' if d.get('top_amount', 0) > 100 else 'silver'\n"
                    "    return record.__class__(\n"
                    "        data=d,\n"
                    "        metadata=record.metadata,\n"
                    "        schema_version=record.schema_version,\n"
                    "    )\n"
                ),
            }
        ],
        "sink": {"connection": "dst", "table": "report_customer_tier", "mode": "append"},
    }

    # ---- Stage 4: SELECT * → products_clone --------------------------
    # SchemaInspector (ADR-0045) expands the star projection at run time.
    p4_cfg = {
        "name": "stage4_clone",
        "source": {"connection": "src", "query": "SELECT * FROM raw_products"},
        "sink": {"connection": "dst", "table": "products_clone", "mode": "append"},
    }

    # Seed the four pipelines + their runs upfront.
    p1, pv1 = await _seed_pipeline(session, workspace_id=ws.id, name="p1", config=p1_cfg)
    p2, pv2 = await _seed_pipeline(session, workspace_id=ws.id, name="p2", config=p2_cfg)
    p3, pv3 = await _seed_pipeline(session, workspace_id=ws.id, name="p3", config=p3_cfg)
    p4, pv4 = await _seed_pipeline(session, workspace_id=ws.id, name="p4", config=p4_cfg)

    # We don't keep references to the Run rows — the executor claim loop
    # below picks each pending row in order, which is exactly what the
    # production worker does.
    for p, pv in ((p1, pv1), (p2, pv2), (p3, pv3), (p4, pv4)):
        await _seed_pending_run(
            session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
        )

    # Claim + execute in order. The pipelines have a data dependency
    # (each stage's source reads what the previous stage wrote) so we
    # must run them serially.
    executor = RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id="dogfood"
    )
    for _ in range(4):
        claimed = await claim_pending_run(session, worker_id="dogfood")
        assert claimed is not None, "expected a pending run to claim"
        await session.commit()
        await executor.execute(claimed.id)

    # ===== Now inspect the catalog =====================================
    repo = AssetRepository(session)
    assets = {a.asset_key: a for a in await repo.list_for_workspace(workspace_id=ws.id)}

    # ---- (a) All asset rows present ---------------------------------
    # Source side — raw tables on ``src`` (Phase X follow-up registered
    # every JOIN'd base) and the intermediate read-sides (stages 2 and 3
    # read what stage 1 / stage 2 wrote, on the ``src`` connection).
    must_have_src = {
        "src/raw_orders",  # stage 1 JOIN
        "src/raw_customers",  # stage 1 JOIN
        "src/raw_products",  # stage 4
        "src/staging_orders_enriched",  # stage 2 source
        "src/mart_top_orders",  # stage 3 source
    }
    # Sink side — each pipeline's output.
    must_have_dst = {
        "dst/staging_orders_enriched",
        "dst/mart_top_orders",
        "dst/report_customer_tier",
        "dst/products_clone",
    }
    missing = (must_have_src | must_have_dst) - set(assets)
    assert not missing, f"missing asset rows: {missing}\nhave: {sorted(assets)}"

    # ---- (b) Asset edges form the expected DAG ----------------------
    async def _upstream_keys(asset_key: str) -> set[str]:
        return {a.asset_key for a in await repo.upstream(assets[asset_key].id)}

    # stage 1: both raw tables feed staging_orders_enriched
    s1_ups = await _upstream_keys("dst/staging_orders_enriched")
    assert {"src/raw_orders", "src/raw_customers"} <= s1_ups, s1_ups

    # stage 2: staging feeds mart
    s2_ups = await _upstream_keys("dst/mart_top_orders")
    assert "src/staging_orders_enriched" in s2_ups, s2_ups

    # stage 3: mart feeds report (passthrough through python transform)
    s3_ups = await _upstream_keys("dst/report_customer_tier")
    assert "src/mart_top_orders" in s3_ups, s3_ups

    # stage 4: raw_products feeds products_clone
    s4_ups = await _upstream_keys("dst/products_clone")
    assert "src/raw_products" in s4_ups, s4_ups

    # ---- (c) No sink is column_lineage_opaque ------------------------
    # Phase Z (SELECT * schema inject) + Phase AA (passthrough fallback)
    # promise that every sink we can read schemas for becomes traceable.
    for k in must_have_dst:
        assert assets[k].column_lineage_opaque is False, (
            f"sink {k} is still marked opaque despite Phase Z/AA"
        )

    # ---- (d) Column lineage rows + upstream attribution -------------
    async def _col_upstreams(asset_key: str) -> dict[str, set[tuple[str, str]]]:
        cols, upstream_map = await repo.column_lineage_for_asset(asset_id=assets[asset_key].id)
        out: dict[str, set[tuple[str, str]]] = {}
        for c in cols:
            out[c.name] = {
                (up_asset.asset_key, up_col.name) for up_col, up_asset in upstream_map[c.id]
            }
        return out

    # Stage 1 — JOIN with CTE wrapping. Every output column resolves to its
    # actual base table on the source connection. ``id`` / ``customer_id`` /
    # ``amount`` come from ``raw_orders`` (through the CTE); ``email`` /
    # ``country`` come from ``raw_customers``.
    s1_cols = await _col_upstreams("dst/staging_orders_enriched")
    assert ("src/raw_orders", "id") in s1_cols["id"], s1_cols
    assert ("src/raw_orders", "customer_id") in s1_cols["customer_id"], s1_cols
    assert ("src/raw_orders", "amount") in s1_cols["amount"], s1_cols
    assert ("src/raw_customers", "email") in s1_cols["email"], s1_cols
    assert ("src/raw_customers", "country") in s1_cols["country"], s1_cols

    # Stage 2 — window + CTE chain. ``customer_id`` and ``top_amount`` both
    # trace back through ``ranked`` to ``staging_orders_enriched``.
    s2_cols = await _col_upstreams("dst/mart_top_orders")
    assert ("src/staging_orders_enriched", "customer_id") in s2_cols["customer_id"]
    assert ("src/staging_orders_enriched", "amount") in s2_cols["top_amount"]

    # Stage 3 — python transform. Static analysis bails; schema passthrough
    # recovers the shared columns. ``tier`` was created in user code so it
    # exists as a column row but has no upstream — exactly the right
    # truthful state.
    s3_cols = await _col_upstreams("dst/report_customer_tier")
    assert ("src/mart_top_orders", "customer_id") in s3_cols["customer_id"]
    assert ("src/mart_top_orders", "top_amount") in s3_cols["top_amount"]
    assert s3_cols.get("tier") == set(), (
        f"tier should exist with no upstream, got {s3_cols.get('tier')!r}"
    )

    # Stage 4 — SELECT * resolved through the sqlite SchemaInspector.
    s4_cols = await _col_upstreams("dst/products_clone")
    for col in ("id", "name", "category", "price"):
        assert ("src/raw_products", col) in s4_cols[col], (col, s4_cols)
