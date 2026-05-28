"""SQL column lineage extractor corpus (ADR-0041 Phase X, 2026-05-28).

Drives :func:`etl_plugins.runtime.sql_lineage.extract_sql_lineage` through a
representative cross-section of real-world SQL constructs:

* simple projection + alias + function
* JOINs (INNER / LEFT / CROSS / USING)
* CTEs (single, chained, recursive)
* subqueries in FROM and SELECT
* UNION ALL
* window functions
* CASE WHEN and COALESCE (multi-source)
* LATERAL joins (Postgres)
* SELECT * with and without schema

Each test compares against the expected ``{output_col: {(table, source_col)}}``
mapping. Sets are used rather than lists because the leaf order is an
implementation detail of ``sqlglot.lineage`` — only the *set* of origins is
load-bearing for the lineage graph.
"""

from __future__ import annotations

from etl_plugins.runtime.sql_lineage import (
    extract_referenced_tables,
    extract_sql_lineage,
)


def _as_sets(
    result: dict[str, list[tuple[str, str]]] | None,
) -> dict[str, set[tuple[str, str]]] | None:
    """Shape adapter — set comparison is order-insensitive."""
    if result is None:
        return None
    return {k: set(v) for k, v in result.items()}


# ---------- baseline projections ----------


def test_simple_select() -> None:
    assert _as_sets(extract_sql_lineage("SELECT a, b FROM t")) == {
        "a": {("t", "a")},
        "b": {("t", "b")},
    }


def test_alias() -> None:
    assert _as_sets(extract_sql_lineage("SELECT a AS x, b AS y FROM t")) == {
        "x": {("t", "a")},
        "y": {("t", "b")},
    }


def test_function_passthrough_traces_argument() -> None:
    assert _as_sets(extract_sql_lineage("SELECT UPPER(name) AS up FROM users")) == {
        "up": {("users", "name")},
    }


def test_arithmetic_keeps_both_operands() -> None:
    assert _as_sets(extract_sql_lineage("SELECT a + b AS s FROM t")) == {
        "s": {("t", "a"), ("t", "b")},
    }


# ---------- joins ----------


def test_inner_join_separates_sources() -> None:
    q = "SELECT t1.a AS x, t2.b AS y FROM t1 JOIN t2 ON t1.id = t2.id"
    assert _as_sets(extract_sql_lineage(q)) == {
        "x": {("t1", "a")},
        "y": {("t2", "b")},
    }


def test_left_join_with_coalesce_is_multi_source() -> None:
    q = "SELECT COALESCE(a.x, b.y) AS v FROM a LEFT JOIN b ON a.id = b.id"
    assert _as_sets(extract_sql_lineage(q)) == {
        "v": {("a", "x"), ("b", "y")},
    }


def test_three_way_join() -> None:
    q = """
    SELECT u.id AS user_id, o.id AS order_id, p.name AS product
    FROM users u
    JOIN orders o ON o.user_id = u.id
    JOIN products p ON p.id = o.product_id
    """
    assert _as_sets(extract_sql_lineage(q)) == {
        "user_id": {("users", "id")},
        "order_id": {("orders", "id")},
        "product": {("products", "name")},
    }


# ---------- CTEs ----------


def test_single_cte() -> None:
    q = "WITH c AS (SELECT col AS a FROM t) SELECT a FROM c"
    assert _as_sets(extract_sql_lineage(q)) == {"a": {("t", "col")}}


def test_chained_cte() -> None:
    q = """
    WITH a AS (SELECT id, name FROM users),
         b AS (SELECT id, name FROM a WHERE id > 0),
         c AS (SELECT id AS uid, UPPER(name) AS nm FROM b)
    SELECT uid, nm FROM c
    """
    assert _as_sets(extract_sql_lineage(q)) == {
        "uid": {("users", "id")},
        "nm": {("users", "name")},
    }


def test_recursive_cte_traces_base_columns() -> None:
    # Recursive CTE: the recursive arm references the CTE itself, so the only
    # column-tracing origin is the base case. ``n + 1`` arithmetic keeps no
    # base column, just the literal anchor — we accept any non-empty resolved
    # set so long as it doesn't crash.
    q = """
    WITH RECURSIVE r(n) AS (
        SELECT 1
        UNION ALL
        SELECT n + 1 FROM r WHERE n < 5
    )
    SELECT n FROM r
    """
    result = extract_sql_lineage(q)
    assert result is not None
    assert "n" in result  # column exists in output, exact origins are implementation-defined


# ---------- subqueries ----------


def test_subquery_in_from() -> None:
    q = "SELECT s.x FROM (SELECT col AS x FROM t) s"
    assert _as_sets(extract_sql_lineage(q)) == {"x": {("t", "col")}}


def test_subquery_in_select() -> None:
    # Alias-disambiguated correlated subquery — the realistic warehouse shape
    # for "per-user max order amount". Without explicit aliases ``sqlglot``
    # treats the inner ``users.id`` reference as ambiguous; aliases (or a
    # schema dict) resolve it, which matches how production SQL is written.
    q = """
    SELECT u.id,
           (SELECT MAX(o.amount) FROM orders o WHERE o.user_id = u.id) AS max_order
    FROM users u
    """
    result = _as_sets(extract_sql_lineage(q))
    assert result is not None
    assert result["id"] == {("users", "id")}
    assert ("orders", "amount") in result["max_order"]


# ---------- UNION ----------


def test_union_all_combines_branches() -> None:
    q = "SELECT id FROM a UNION ALL SELECT id FROM b"
    assert _as_sets(extract_sql_lineage(q)) == {
        "id": {("a", "id"), ("b", "id")},
    }


def test_union_with_different_source_columns() -> None:
    q = "SELECT id AS i FROM a UNION ALL SELECT user_id AS i FROM b"
    assert _as_sets(extract_sql_lineage(q)) == {
        "i": {("a", "id"), ("b", "user_id")},
    }


# ---------- windows ----------


def test_window_function_keeps_partition_and_value() -> None:
    q = "SELECT customer, SUM(amount) OVER (PARTITION BY customer) AS s FROM orders"
    result = _as_sets(extract_sql_lineage(q))
    assert result is not None
    assert result["customer"] == {("orders", "customer")}
    # The window's value column AND the partition column both feed ``s``.
    assert ("orders", "amount") in result["s"]
    assert ("orders", "customer") in result["s"]


def test_row_number_pattern() -> None:
    q = """
    WITH ranked AS (
        SELECT order_id, customer, amount,
               ROW_NUMBER() OVER (PARTITION BY customer ORDER BY amount DESC) AS rnk
        FROM orders
    )
    SELECT order_id, customer, amount FROM ranked WHERE rnk = 1
    """
    assert _as_sets(extract_sql_lineage(q)) == {
        "order_id": {("orders", "order_id")},
        "customer": {("orders", "customer")},
        "amount": {("orders", "amount")},
    }


# ---------- CASE WHEN ----------


def test_case_when_collects_every_branch() -> None:
    q = "SELECT CASE WHEN t.flag THEN t.a ELSE t.b END AS v FROM t"
    assert _as_sets(extract_sql_lineage(q)) == {
        "v": {("t", "flag"), ("t", "a"), ("t", "b")},
    }


# ---------- LATERAL (Postgres) ----------


def test_lateral_join_postgres() -> None:
    q = """
    SELECT u.id, s.total
    FROM users u
    CROSS JOIN LATERAL (
        SELECT SUM(o.amount) AS total FROM orders o WHERE o.user_id = u.id
    ) s
    """
    result = _as_sets(extract_sql_lineage(q, dialect="postgres"))
    assert result is not None
    assert result["id"] == {("users", "id")}
    # The lateral subquery sums orders.amount; lineage should reach it. The
    # correlated reference to users.id may also show up depending on sqlglot's
    # resolution depth, which is fine.
    assert ("orders", "amount") in result["total"]


# ---------- SELECT * handling ----------


def test_select_star_without_schema_is_opaque() -> None:
    assert extract_sql_lineage("SELECT * FROM t") is None


def test_select_star_with_schema_expands() -> None:
    schema = {"t": {"a": "INT", "b": "TEXT"}}
    assert _as_sets(extract_sql_lineage("SELECT * FROM t", schema=schema)) == {
        "a": {("t", "a")},
        "b": {("t", "b")},
    }


def test_qualified_star_with_schema() -> None:
    schema = {"orders": {"id": "INT", "amount": "DECIMAL"}}
    result = _as_sets(extract_sql_lineage("SELECT o.* FROM orders o", schema=schema))
    assert result == {
        "id": {("orders", "id")},
        "amount": {("orders", "amount")},
    }


# ---------- defensive ----------


def test_unparseable_returns_none() -> None:
    assert extract_sql_lineage("this is not sql at all !!") is None


def test_non_select_returns_none() -> None:
    assert extract_sql_lineage("INSERT INTO t (a) VALUES (1)") is None


def test_empty_query_returns_none() -> None:
    assert extract_sql_lineage("") is None


# ---------- combined complexity ----------


# ---------- extract_referenced_tables ----------


def test_referenced_tables_simple() -> None:
    assert extract_referenced_tables("SELECT a FROM t") == ["t"]


def test_referenced_tables_join_preserves_order() -> None:
    assert extract_referenced_tables("SELECT t1.a, t2.b FROM t1 JOIN t2 ON t1.id = t2.id") == [
        "t1",
        "t2",
    ]


def test_referenced_tables_cte_excludes_cte_names() -> None:
    # ``c`` is a CTE, not an asset — only the base ``raw`` should land in the
    # input set. The same goes for chained CTEs that reference each other.
    assert extract_referenced_tables("WITH c AS (SELECT a FROM raw) SELECT a FROM c") == ["raw"]
    assert extract_referenced_tables(
        """
        WITH a AS (SELECT * FROM x),
             b AS (SELECT * FROM a)
        SELECT * FROM b
        """
    ) == ["x"]


def test_referenced_tables_union_picks_both_branches() -> None:
    assert extract_referenced_tables("SELECT * FROM t1 UNION ALL SELECT * FROM t2") == ["t1", "t2"]


def test_referenced_tables_correlated_subquery() -> None:
    # The subquery reads ``orders``, the outer query reads ``users``. Both
    # belong in the input asset set.
    tables = extract_referenced_tables(
        """
        SELECT u.id,
               (SELECT MAX(o.amount) FROM orders o WHERE o.user_id = u.id) AS m
        FROM users u
        """
    )
    assert set(tables) == {"users", "orders"}


def test_referenced_tables_non_sql_returns_empty() -> None:
    # ``source.query`` for a Mongo connector is a collection name string —
    # un-parseable as SQL. We should silently report "no extra tables" so the
    # caller falls back to the primary-key derivation.
    assert extract_referenced_tables("not sql at all") == []
    assert extract_referenced_tables("") == []


def test_cte_join_window_and_case_combined() -> None:
    """A query that combines every nontrivial construct above. The lineage
    should still resolve cleanly to leaf columns on the base tables."""
    q = """
    WITH high_value AS (
        SELECT o.user_id, o.amount, o.created_at,
               ROW_NUMBER() OVER (PARTITION BY o.user_id ORDER BY o.amount DESC) AS rk
        FROM orders o
        WHERE o.amount > 0
    ),
    enriched AS (
        SELECT u.id, u.country, hv.amount, hv.created_at,
               CASE WHEN u.country = 'KR' THEN 'domestic' ELSE 'foreign' END AS bucket
        FROM users u
        LEFT JOIN high_value hv ON hv.user_id = u.id AND hv.rk = 1
    )
    SELECT id, country, amount, created_at, bucket
    FROM enriched
    """
    result = _as_sets(extract_sql_lineage(q))
    assert result is not None
    assert result["id"] == {("users", "id")}
    assert result["country"] == {("users", "country")}
    assert result["amount"] == {("orders", "amount")}
    assert result["created_at"] == {("orders", "created_at")}
    # ``bucket`` traces back to ``users.country`` (the CASE input).
    assert ("users", "country") in result["bucket"]
