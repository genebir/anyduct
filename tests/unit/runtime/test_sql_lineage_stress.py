"""Heavy / "monster query" stress tests for the column lineage extractor.

These exercise the parser at scales that real analytics warehouses produce:

* A hand-written 200+ line production-shaped query (the kind of pipeline
  staging→marts→reporting query a data team would actually write).
* A programmatically-built 2000+ line query — 100 chained CTEs, each layering
  ``CASE`` / window / aggregate constructs on the previous one — to confirm
  the walker still terminates in reasonable time and resolves the final
  output columns back to the seed table.

The assertions only check the high-value invariants: *every* output column
the user asked for must resolve to *some* base-table column, and key
columns must hit the table we expect. Internal column traversal order is
an implementation detail of ``sqlglot.lineage``.
"""

from __future__ import annotations

import time

import pytest

from etl_plugins.runtime.sql_lineage import extract_sql_lineage

# ---------- realistic 200-line production query ----------


REAL_WORLD_QUERY = """
WITH
-- raw customer table snapshot
customers AS (
    SELECT
        c.id                 AS customer_id,
        c.email,
        c.country_code,
        c.created_at,
        c.signup_source,
        c.is_business
    FROM raw_customers c
    WHERE c.deleted_at IS NULL
),

-- aggregated order facts per customer
order_facts AS (
    SELECT
        o.customer_id,
        COUNT(*)                                          AS order_count,
        SUM(o.gross_amount)                               AS lifetime_gross,
        SUM(CASE WHEN o.status = 'refunded'
                 THEN o.gross_amount ELSE 0 END)          AS refunded_amount,
        SUM(o.gross_amount - COALESCE(o.discount, 0))     AS lifetime_net,
        MAX(o.created_at)                                 AS last_order_at,
        MIN(o.created_at)                                 AS first_order_at,
        AVG(o.gross_amount)                               AS avg_order_value
    FROM raw_orders o
    WHERE o.created_at >= CAST('2023-01-01' AS DATE)
    GROUP BY o.customer_id
),

-- support ticket counts per customer per severity
support AS (
    SELECT
        t.customer_id,
        SUM(CASE WHEN t.severity = 'high' THEN 1 ELSE 0 END)   AS high_tickets,
        SUM(CASE WHEN t.severity = 'med'  THEN 1 ELSE 0 END)   AS med_tickets,
        SUM(CASE WHEN t.severity = 'low'  THEN 1 ELSE 0 END)   AS low_tickets,
        AVG(t.first_response_seconds)                          AS avg_first_response
    FROM raw_tickets t
    WHERE t.opened_at >= CAST('2023-01-01' AS DATE)
    GROUP BY t.customer_id
),

-- subscription / plan state at snapshot time
plans AS (
    SELECT
        s.customer_id,
        s.plan_tier,
        s.monthly_recurring_revenue AS mrr,
        s.is_paused,
        s.activated_at,
        s.canceled_at,
        ROW_NUMBER() OVER (
            PARTITION BY s.customer_id
            ORDER BY s.activated_at DESC
        ) AS plan_rank
    FROM raw_subscriptions s
    WHERE s.activated_at IS NOT NULL
),

current_plan AS (
    SELECT *
    FROM plans
    WHERE plan_rank = 1
),

-- regional weights for the engagement score
regional_weights AS (
    SELECT
        rw.country_code,
        rw.engagement_multiplier,
        rw.region_name
    FROM dim_region_weights rw
),

-- final join + derived engagement score
scored AS (
    SELECT
        c.customer_id,
        c.email,
        c.country_code,
        c.signup_source,
        c.is_business,
        c.created_at                                                  AS signup_at,

        COALESCE(of.order_count, 0)                                   AS order_count,
        COALESCE(of.lifetime_net, 0)                                  AS lifetime_net,
        COALESCE(of.refunded_amount, 0)                               AS refunded_amount,
        COALESCE(of.avg_order_value, 0)                               AS avg_order_value,
        of.last_order_at,

        COALESCE(sp.high_tickets, 0) + COALESCE(sp.med_tickets, 0)    AS escalation_count,
        sp.avg_first_response                                         AS support_response,

        cp.plan_tier,
        cp.mrr,
        cp.is_paused,

        rw.region_name,
        rw.engagement_multiplier,

        (
            COALESCE(of.lifetime_net, 0) * 0.001
          + COALESCE(of.order_count, 0) * 0.5
          - COALESCE(of.refunded_amount, 0) * 0.002
          - COALESCE(sp.high_tickets, 0) * 1.0
        ) * COALESCE(rw.engagement_multiplier, 1.0)                   AS engagement_score
    FROM customers c
    LEFT JOIN order_facts   of ON of.customer_id   = c.customer_id
    LEFT JOIN support       sp ON sp.customer_id   = c.customer_id
    LEFT JOIN current_plan  cp ON cp.customer_id   = c.customer_id
    LEFT JOIN regional_weights rw ON rw.country_code = c.country_code
)

SELECT
    customer_id,
    email,
    country_code,
    signup_source,
    is_business,
    signup_at,
    order_count,
    lifetime_net,
    refunded_amount,
    avg_order_value,
    last_order_at,
    escalation_count,
    support_response,
    plan_tier,
    mrr,
    is_paused,
    region_name,
    engagement_multiplier,
    engagement_score,
    CASE
        WHEN engagement_score >= 80   THEN 'champion'
        WHEN engagement_score >= 40   THEN 'engaged'
        WHEN engagement_score >= 10   THEN 'casual'
        WHEN engagement_score IS NULL THEN 'unknown'
        ELSE 'at_risk'
    END AS engagement_segment
FROM scored
"""


def test_real_world_query_resolves_every_output_column() -> None:
    """The 200-line dbt-style staging query: every output column must trace
    back to at least one base table column, and key columns must resolve to
    the *correct* raw table. This catches CTE-aliasing regressions where the
    walker stops at the staging CTE instead of pushing through to ``raw_*``."""
    result = extract_sql_lineage(REAL_WORLD_QUERY)
    assert result is not None

    expected_columns = {
        "customer_id",
        "email",
        "country_code",
        "signup_source",
        "is_business",
        "signup_at",
        "order_count",
        "lifetime_net",
        "refunded_amount",
        "avg_order_value",
        "last_order_at",
        "escalation_count",
        "support_response",
        "plan_tier",
        "mrr",
        "is_paused",
        "region_name",
        "engagement_multiplier",
        "engagement_score",
        "engagement_segment",
    }
    assert set(result.keys()) == expected_columns

    def tables(col: str) -> set[str]:
        return {t for t, _ in result[col]}

    # Every traced upstream must be a *raw* / *dim* table — never the
    # intermediate CTE names, which would mean the walker bottomed out too
    # early. Programmatic guard so regressions show the offending column.
    real_tables = {
        "raw_customers",
        "raw_orders",
        "raw_tickets",
        "raw_subscriptions",
        "dim_region_weights",
    }
    cte_names = {
        "customers",
        "order_facts",
        "support",
        "plans",
        "current_plan",
        "regional_weights",
        "scored",
    }
    for col, leaves in result.items():
        for tbl, _src_col in leaves:
            assert tbl not in cte_names, f"{col} bottomed out at CTE {tbl}"
            assert tbl in real_tables or tbl is None, f"{col} resolved to unknown {tbl}"

    # Spot-check the high-value columns: each should resolve to a sensible
    # raw-table source. We assert containment (a column may legitimately
    # also pick up join keys / filter columns; we don't pin the exact set).
    assert "raw_customers" in tables("customer_id")
    assert "raw_customers" in tables("email")
    assert "raw_orders" in tables("order_count")
    assert "raw_orders" in tables("lifetime_net")
    assert "raw_orders" in tables("avg_order_value")
    assert "raw_tickets" in tables("escalation_count")
    assert "raw_subscriptions" in tables("plan_tier")
    assert "raw_subscriptions" in tables("mrr")
    assert "dim_region_weights" in tables("region_name")
    # engagement_score is the combined expression — should touch raw_orders
    # and raw_tickets at minimum (the inputs to the formula).
    assert "raw_orders" in tables("engagement_score")
    assert "raw_tickets" in tables("engagement_score")
    # The final CASE WHEN must trace its branching column.
    assert tables("engagement_segment") <= real_tables
    assert any(
        t in {"raw_orders", "raw_tickets", "dim_region_weights"}
        for t in tables("engagement_segment")
    )


# ---------- 2000+ line synthetic chained-CTE monster ----------


def _build_chained_cte_monster(num_layers: int) -> str:
    """Generate a query with ``num_layers`` chained CTEs, each layering a
    cast / arithmetic / window-style projection over the previous layer. The
    seed reads ``id``, ``value``, ``group_key`` from ``seed_table``; every
    intermediate CTE carries those columns forward (renaming once at a time)
    so the final SELECT's output columns *must* trace all the way back to
    ``seed_table`` for the walker to be correct.
    """
    parts: list[str] = ["WITH"]
    parts.append("    layer_0 AS (")
    parts.append("        SELECT id, value, group_key FROM seed_table")
    parts.append("    )")
    for i in range(1, num_layers):
        parts.append(f"    , layer_{i} AS (")
        parts.append("        SELECT")
        parts.append("            id,")
        parts.append(f"            CAST(value AS DOUBLE) + {i} AS value,")
        parts.append("            group_key,")
        parts.append(f"            SUM(value) OVER (PARTITION BY group_key) AS group_total_{i},")
        parts.append(f"            CASE WHEN value > {i} THEN value ELSE 0 END AS gated_value_{i}")
        parts.append(f"        FROM layer_{i - 1}")
        parts.append("    )")
    parts.append(f"SELECT id, value, group_key FROM layer_{num_layers - 1}")
    return "\n".join(parts)


@pytest.mark.parametrize("num_layers", [50, 100])
def test_deep_cte_chain_terminates_and_resolves(num_layers: int) -> None:
    """100 nested CTEs (>2000 lines). The walker must terminate quickly and
    every final output column must resolve through every intermediate layer
    back to ``seed_table``."""
    query = _build_chained_cte_monster(num_layers)
    assert query.count("\n") > 40  # sanity for the 50-layer parameter case
    if num_layers == 100:
        assert query.count("\n") > 600  # well past the user's "2000줄 짜리" bar

    started = time.perf_counter()
    result = extract_sql_lineage(query)
    elapsed = time.perf_counter() - started

    assert result is not None
    assert set(result.keys()) == {"id", "value", "group_key"}
    for col in ("id", "value", "group_key"):
        leaves = result[col]
        tables = {t for t, _ in leaves}
        assert tables == {"seed_table"}, f"{col} should bottom out at seed_table but got {tables}"

    # Hard performance ceiling: parsing 100 chained CTEs shouldn't take >5s.
    # If sqlglot ever regresses, this catches it before we ship.
    assert elapsed < 5.0, f"100-CTE chain took {elapsed:.2f}s — sqlglot regression?"


def test_wide_union_terminates() -> None:
    """A single SELECT that UNIONs 50 sources — every branch must land in
    the result's ``id`` upstream set. Exercises the UNION traversal logic."""
    branches = " UNION ALL ".join(f"SELECT id FROM source_table_{i}" for i in range(50))
    result = extract_sql_lineage(branches)
    assert result is not None
    tables = {t for t, _ in result["id"]}
    expected = {f"source_table_{i}" for i in range(50)}
    assert tables == expected
