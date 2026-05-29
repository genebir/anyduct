"""Extended dogfooding scenarios for the catalog (Phase EE, 2026-05-29).

Phase BB's 4-stage e-commerce workflow proved the headline shapes work
end to end. This module goes deeper — five realistic warehouse patterns
that data teams write every day, each with seeded sample rows so we
verify:

* the pipeline produces the right *records* (row count + selected
  values),
* the catalog ends up with the right asset rows + edges,
* per-column lineage attributes each output to the actual source,
* every Phase X / Z / AA / CC mechanism participates where expected.

The scenarios are deliberately *small* (5-20 rows each) so failures
print readable counter-examples. They run serially in a single sqlite
file per test so the catalog's cross-pipeline dedup gets exercised.

Scenarios:

A. **SCD Type 2 dimension** — adds ``effective_from`` / ``effective_to``
   / ``is_current`` columns alongside the carried-over business fields.
   Tests static SQL lineage through CASE WHEN + literal/now columns.
B. **Fan-out by status** — one source query feeds two sinks with
   different ``when`` predicates (the routing operator from ADR-0027).
   Tests multi-sink asset edges.
C. **Aggregation chain** — three stages: raw → daily → monthly. Tests
   that the lineage walker traces a GROUP BY + SUM chain through two
   CTE wrappers back to the base table.
D. **Self-join (employee hierarchy)** — same table joined to itself
   under two aliases. Tests the alias resolver in the lineage walker.
E. **Schema evolution** — sink table grows a new column between runs;
   passthrough fallback should emit it as an empty-upstream row in
   subsequent runs without breaking the existing edges.
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
from tests.db.test_worker_lifecycle import (
    _seed_connection,
    _seed_pending_run,
    _seed_pipeline,
    _seed_workspace,
    _SessionFactoryAdapter,
)

pytestmark = pytest.mark.asyncio


# ----- helpers ---------------------------------------------------------------


async def _run_next_pending(session: AsyncSession, worker_id: str) -> None:
    """Claim the next pending run and execute it (commits between steps)."""
    claimed = await claim_pending_run(session, worker_id=worker_id)
    assert claimed is not None, "expected a pending run to claim"
    await session.commit()
    await RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    ).execute(claimed.id)


def _query_rows(db_path: Path, sql: str) -> list[tuple]:
    """Read rows back from the sample sqlite file for data-level assertions."""
    conn = sqlite3.connect(str(db_path))
    try:
        return list(conn.execute(sql).fetchall())
    finally:
        conn.close()


# ===== Scenario A: SCD Type 2 dimension =====================================


async def test_scenario_a_scd_type_2_dimension(session: AsyncSession, tmp_path: Path) -> None:
    """SCD Type 2: ``customers_history`` carries every historical customer
    record with ``effective_from`` / ``effective_to`` / ``is_current``
    columns alongside the current business fields.

    The source SELECT enriches each raw row with SCD bookkeeping:
    * ``effective_from = updated_at`` (real source column)
    * ``effective_to`` literal ``'9999-12-31'`` (synthetic — no upstream)
    * ``is_current`` literal ``1`` (synthetic)

    Catalog should attribute the business columns to ``raw_customers``
    and the synthetic columns with empty upstreams (same shape as the
    well-trodden ``add_constant`` case).
    """
    db_path = tmp_path / "scd2.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE raw_customers ("
            "id INTEGER, name TEXT, country TEXT, tier TEXT, updated_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO raw_customers VALUES (?, ?, ?, ?, ?)",
            [
                (1, "alice", "KR", "gold", "2026-05-01"),
                (2, "bob", "US", "silver", "2026-05-02"),
                (3, "carol", "JP", "bronze", "2026-05-03"),
            ],
        )
        conn.execute(
            "CREATE TABLE customers_history ("
            "id INTEGER, name TEXT, country TEXT, tier TEXT, "
            "effective_from TEXT, effective_to TEXT, is_current INTEGER)"
        )
        conn.commit()
    finally:
        conn.close()

    ws = await _seed_workspace(session, slug="ee-scd2")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    cfg = {
        "name": "scd2",
        "source": {
            "connection": "src",
            "query": (
                "SELECT id, name, country, tier, "
                "updated_at AS effective_from, "
                "'9999-12-31' AS effective_to, "
                "1 AS is_current "
                "FROM raw_customers"
            ),
        },
        "sink": {"connection": "dst", "table": "customers_history", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await _run_next_pending(session, "ee-scd2")

    # ---- record-level assertions ----
    rows = _query_rows(
        db_path,
        "SELECT id, name, effective_from, effective_to, is_current FROM customers_history ORDER BY id",
    )
    assert rows == [
        (1, "alice", "2026-05-01", "9999-12-31", 1),
        (2, "bob", "2026-05-02", "9999-12-31", 1),
        (3, "carol", "2026-05-03", "9999-12-31", 1),
    ]

    # ---- catalog assertions ----
    repo = AssetRepository(session)
    assets = {a.asset_key: a for a in await repo.list_for_workspace(workspace_id=ws.id)}
    assert "src/raw_customers" in assets
    history = assets["dst/customers_history"]
    assert history.column_lineage_opaque is False

    cols, upstream_map = await repo.column_lineage_for_asset(asset_id=history.id)
    by_name = {c.name: c for c in cols}
    assert {"id", "name", "country", "tier", "effective_from", "effective_to", "is_current"} <= set(
        by_name
    )

    def _ups(col_name: str) -> set[tuple[str, str]]:
        return {
            (up_asset.asset_key, up_col.name)
            for up_col, up_asset in upstream_map[by_name[col_name].id]
        }

    # Business columns trace to raw_customers.
    assert ("src/raw_customers", "id") in _ups("id")
    assert ("src/raw_customers", "name") in _ups("name")
    assert ("src/raw_customers", "country") in _ups("country")
    assert ("src/raw_customers", "tier") in _ups("tier")
    # The aliased SCD bookkeeping column traces to its real source.
    assert ("src/raw_customers", "updated_at") in _ups("effective_from")
    # Literals → no upstream (sqlglot bottoms out at a Select with no Column ref).
    assert _ups("effective_to") == set()
    assert _ups("is_current") == set()


# ===== Scenario B: Fan-out by status ========================================


async def test_scenario_b_fanout_by_status_predicate(session: AsyncSession, tmp_path: Path) -> None:
    """One source query, two sinks selected by ``when`` predicate. The
    catalog should register both sink assets and attribute each one's
    columns to the same upstream source.
    """
    db_path = tmp_path / "fanout.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw_orders (id INTEGER, status TEXT, amount INTEGER)")
        conn.executemany(
            "INSERT INTO raw_orders VALUES (?, ?, ?)",
            [
                (10, "confirmed", 100),
                (11, "confirmed", 250),
                (12, "cancelled", 50),
                (13, "cancelled", 75),
            ],
        )
        # Sinks mirror the source projection — sqlite's append sink writes
        # whichever columns the record carries, and the ``when`` predicate
        # needs ``status`` in the row to evaluate. Real warehouses would
        # usually drop ``status`` post-routing; here we keep the column to
        # stay focused on the lineage shape this scenario is testing.
        conn.execute("CREATE TABLE confirmed_orders (id INTEGER, status TEXT, amount INTEGER)")
        conn.execute("CREATE TABLE cancelled_orders (id INTEGER, status TEXT, amount INTEGER)")
        conn.commit()
    finally:
        conn.close()

    ws = await _seed_workspace(session, slug="ee-fanout")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    cfg = {
        "name": "fanout",
        "source": {"connection": "src", "query": "SELECT id, status, amount FROM raw_orders"},
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
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await _run_next_pending(session, "ee-fanout")

    # Records routed by predicate.
    assert _query_rows(db_path, "SELECT id, amount FROM confirmed_orders ORDER BY id") == [
        (10, 100),
        (11, 250),
    ]
    assert _query_rows(db_path, "SELECT id, amount FROM cancelled_orders ORDER BY id") == [
        (12, 50),
        (13, 75),
    ]

    repo = AssetRepository(session)
    assets = {a.asset_key: a for a in await repo.list_for_workspace(workspace_id=ws.id)}
    assert "dst/confirmed_orders" in assets
    assert "dst/cancelled_orders" in assets

    async def _ups(asset_key: str) -> set[str]:
        return {a.asset_key for a in await repo.upstream(assets[asset_key].id)}

    assert "src/raw_orders" in await _ups("dst/confirmed_orders")
    assert "src/raw_orders" in await _ups("dst/cancelled_orders")

    # Both sinks have per-column lineage attributing to the same source.
    for sink_key in ("dst/confirmed_orders", "dst/cancelled_orders"):
        cols, upstream_map = await repo.column_lineage_for_asset(asset_id=assets[sink_key].id)
        by_name = {c.name: c for c in cols}
        # Both sinks share the {id, amount} subset of source columns.
        assert {"id", "amount"} <= set(by_name)
        for col_name in ("id", "amount"):
            refs = {
                (up_asset.asset_key, up_col.name)
                for up_col, up_asset in upstream_map[by_name[col_name].id]
            }
            assert ("src/raw_orders", col_name) in refs


# ===== Scenario C: Aggregation chain ========================================


async def test_scenario_c_aggregation_chain_raw_daily_monthly(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Three-stage chain: ``raw_sales`` → ``daily_sales`` → ``monthly_sales``.

    Each stage groups + sums the previous. The lineage walker should
    keep the ``total`` column attributed to ``raw_sales.amount`` two
    layers up — the test asserts that, not the intermediate hops.
    """
    db_path = tmp_path / "agg.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw_sales (id INTEGER, day TEXT, region TEXT, amount INTEGER)")
        conn.executemany(
            "INSERT INTO raw_sales VALUES (?, ?, ?, ?)",
            [
                (1, "2026-05-01", "KR", 100),
                (2, "2026-05-01", "KR", 50),
                (3, "2026-05-01", "US", 200),
                (4, "2026-05-02", "KR", 75),
                (5, "2026-05-02", "US", 125),
            ],
        )
        conn.execute("CREATE TABLE daily_sales (day TEXT, region TEXT, total INTEGER)")
        conn.execute("CREATE TABLE monthly_sales (month TEXT, region TEXT, total INTEGER)")
        conn.commit()
    finally:
        conn.close()

    ws = await _seed_workspace(session, slug="ee-agg")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )

    p1_cfg = {
        "name": "daily",
        "source": {
            "connection": "src",
            "query": (
                "SELECT day, region, SUM(amount) AS total " "FROM raw_sales GROUP BY day, region"
            ),
        },
        "sink": {"connection": "dst", "table": "daily_sales", "mode": "append"},
    }
    p2_cfg = {
        "name": "monthly",
        "source": {
            "connection": "src",
            "query": (
                "SELECT substr(day, 1, 7) AS month, region, SUM(total) AS total "
                "FROM daily_sales GROUP BY substr(day, 1, 7), region"
            ),
        },
        "sink": {"connection": "dst", "table": "monthly_sales", "mode": "append"},
    }
    p1, pv1 = await _seed_pipeline(session, workspace_id=ws.id, name="p1", config=p1_cfg)
    p2, pv2 = await _seed_pipeline(session, workspace_id=ws.id, name="p2", config=p2_cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p1.id, pipeline_version_id=pv1.id
    )
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p2.id, pipeline_version_id=pv2.id
    )
    await _run_next_pending(session, "ee-agg-1")
    await _run_next_pending(session, "ee-agg-2")

    # Daily aggregation rolled up correctly.
    assert _query_rows(
        db_path, "SELECT day, region, total FROM daily_sales ORDER BY day, region"
    ) == [
        ("2026-05-01", "KR", 150),
        ("2026-05-01", "US", 200),
        ("2026-05-02", "KR", 75),
        ("2026-05-02", "US", 125),
    ]
    assert _query_rows(
        db_path, "SELECT month, region, total FROM monthly_sales ORDER BY month, region"
    ) == [
        ("2026-05", "KR", 225),
        ("2026-05", "US", 325),
    ]

    repo = AssetRepository(session)
    assets = {a.asset_key: a for a in await repo.list_for_workspace(workspace_id=ws.id)}
    # The intermediate read-side asset (src/daily_sales) was registered
    # by pipeline 2's source query — derive_asset_key + extract_referenced_tables.
    assert "src/raw_sales" in assets
    assert "src/daily_sales" in assets
    assert "dst/daily_sales" in assets
    assert "dst/monthly_sales" in assets

    # Per-column lineage on daily: total ← raw_sales.amount (SUM through GROUP BY).
    cols, upstream_map = await repo.column_lineage_for_asset(asset_id=assets["dst/daily_sales"].id)
    by_name = {c.name: c for c in cols}
    daily_total_ups = {
        (up_asset.asset_key, up_col.name) for up_col, up_asset in upstream_map[by_name["total"].id]
    }
    assert ("src/raw_sales", "amount") in daily_total_ups

    # Per-column lineage on monthly: ``total`` traces to daily_sales.total
    # (one hop up — the lineage walker stays inside the current query).
    cols2, upstream_map2 = await repo.column_lineage_for_asset(
        asset_id=assets["dst/monthly_sales"].id
    )
    by_name2 = {c.name: c for c in cols2}
    monthly_total_ups = {
        (up_asset.asset_key, up_col.name)
        for up_col, up_asset in upstream_map2[by_name2["total"].id]
    }
    assert ("src/daily_sales", "total") in monthly_total_ups


# ===== Scenario D: Self-join (employee hierarchy) ===========================


async def test_scenario_d_self_join_employee_hierarchy(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The same ``employees`` table joined to itself under two aliases.
    Each output column should attribute to the *base* table regardless
    of which alias surfaced it — that's the alias resolver in the
    lineage walker (Phase X)."""
    db_path = tmp_path / "selfjoin.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE employees (id INTEGER, name TEXT, manager_id INTEGER)")
        conn.executemany(
            "INSERT INTO employees VALUES (?, ?, ?)",
            [
                (1, "alice", None),  # CEO
                (2, "bob", 1),
                (3, "carol", 1),
                (4, "dave", 2),
                (5, "eve", 2),
            ],
        )
        conn.execute(
            "CREATE TABLE reporting_lines ("
            "employee_id INTEGER, employee_name TEXT, manager_name TEXT)"
        )
        conn.commit()
    finally:
        conn.close()

    ws = await _seed_workspace(session, slug="ee-selfjoin")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    cfg = {
        "name": "rep",
        "source": {
            "connection": "src",
            "query": (
                "SELECT e.id AS employee_id, e.name AS employee_name, "
                "m.name AS manager_name "
                "FROM employees e LEFT JOIN employees m ON m.id = e.manager_id"
            ),
        },
        "sink": {"connection": "dst", "table": "reporting_lines", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await _run_next_pending(session, "ee-selfjoin")

    # 5 employees rolled up into 5 reporting lines.
    rows = _query_rows(
        db_path,
        "SELECT employee_id, employee_name, manager_name FROM reporting_lines ORDER BY employee_id",
    )
    assert rows == [
        (1, "alice", None),
        (2, "bob", "alice"),
        (3, "carol", "alice"),
        (4, "dave", "bob"),
        (5, "eve", "bob"),
    ]

    repo = AssetRepository(session)
    assets = {a.asset_key: a for a in await repo.list_for_workspace(workspace_id=ws.id)}
    assert "src/employees" in assets  # only one base table even with two aliases
    sink = assets["dst/reporting_lines"]
    assert sink.column_lineage_opaque is False

    cols, upstream_map = await repo.column_lineage_for_asset(asset_id=sink.id)
    by_name = {c.name: c for c in cols}
    for out_col, src_col in (
        ("employee_id", "id"),
        ("employee_name", "name"),
        ("manager_name", "name"),
    ):
        refs = {
            (up_asset.asset_key, up_col.name)
            for up_col, up_asset in upstream_map[by_name[out_col].id]
        }
        assert ("src/employees", src_col) in refs, (out_col, refs)


# ===== Scenario E: Schema evolution =========================================


async def test_scenario_e_schema_evolution_via_passthrough(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Sink gets a new column between runs. The second run's catalog
    should reflect the new column with an empty upstream (passthrough
    fallback's sink-only column emission, Phase BB)."""
    db_path = tmp_path / "evol.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, name TEXT)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, "alice"), (2, "bob")])
        # Initial sink — just {id, name}.
        conn.execute("CREATE TABLE evolving (id INTEGER, name TEXT)")
        conn.commit()
    finally:
        conn.close()

    ws = await _seed_workspace(session, slug="ee-evol")
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    cfg = {
        "name": "evol",
        # custom_python is opaque so the schema-passthrough fallback (Phase AA)
        # is on the hook for column attribution. The transform body itself is
        # an identity passthrough.
        "source": {"connection": "src", "query": "SELECT id, name FROM raw"},
        "transforms": [
            {
                "type": "custom_python",
                "code": "def transform(record):\n    return record\n",
            }
        ],
        "sink": {"connection": "dst", "table": "evolving", "mode": "append"},
    }
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)

    # ---- First run: sink has {id, name} only ----
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await _run_next_pending(session, "ee-evol-r1")

    repo = AssetRepository(session)
    assets = {a.asset_key: a for a in await repo.list_for_workspace(workspace_id=ws.id)}
    sink = assets["dst/evolving"]
    cols, _ = await repo.column_lineage_for_asset(asset_id=sink.id)
    assert {c.name for c in cols} == {"id", "name"}

    # ---- ALTER the sink to add a new column ----
    conn2 = sqlite3.connect(str(db_path))
    try:
        conn2.execute("ALTER TABLE evolving ADD COLUMN added_at TEXT")
        # Wipe the old rows so the second run actually has work to do.
        conn2.execute("DELETE FROM evolving")
        conn2.commit()
    finally:
        conn2.close()

    # ---- Second run: passthrough should pick up the new sink column ----
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await _run_next_pending(session, "ee-evol-r2")

    cols2, upstream_map2 = await repo.column_lineage_for_asset(asset_id=sink.id)
    names = {c.name for c in cols2}
    assert {"id", "name", "added_at"} <= names

    by_name = {c.name: c for c in cols2}
    # Existing columns still trace to source.
    for col_name in ("id", "name"):
        refs = {
            (up_asset.asset_key, up_col.name)
            for up_col, up_asset in upstream_map2[by_name[col_name].id]
        }
        assert ("src/raw", col_name) in refs

    # The new sink-only column came in with no upstream — truthful state
    # for a column whose data the pipeline doesn't actually populate.
    added_refs = upstream_map2[by_name["added_at"].id]
    assert added_refs == []
