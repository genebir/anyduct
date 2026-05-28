"""Programmatic SQL corpus generator for the lineage fuzz suite (Phase X).

The point of this module is *not* to be a clever SQL fuzzer — it is to
synthesise a large, *expected-known* corpus, so a property test can compare
``extract_sql_lineage(query)`` against a ground-truth lineage map that the
generator built alongside the SQL text.

Each generator returns ``(sql, expected)`` where ``expected`` is the
``{output_column: set[(table, source_column)]}`` mapping the lineage walker
*must* produce. We bias every generator towards realism over edge-case
torture so the corpus exercises the same shapes a warehouse user writes
(staging→marts CTEs, alias-disambiguated JOINs, window-rank patterns).

Generators are organised low-to-high complexity:
    L1  baseline           — simple SELECT, alias, function, arithmetic
    L2  predicates / grouping — WHERE, GROUP BY, HAVING, ORDER BY, LIMIT
    L3  joins              — INNER/LEFT/RIGHT/FULL/3-way
    L4  CTEs               — single, chained, deeply-chained
    L5  subqueries         — FROM-subquery, correlated, nested
    L6  set ops            — UNION, UNION ALL with mismatched aliases
    L7  window functions   — partition + order + frame
    L8  conditional        — CASE WHEN, COALESCE, NULLIF
    L9  combined           — every shape in one query

The whole module is deterministic for a given seed (``Random(seed)``) so
test failures are reproducible without saving the failing SQL.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from random import Random


@dataclass(frozen=True)
class GeneratedQuery:
    """One generated query + its known lineage shape + a complexity tag.

    The ``shape`` field labels the generator that produced the query, so
    when fuzz_test reports failures we know which family of constructs
    needs attention.
    """

    sql: str
    expected: dict[str, set[tuple[str, str]]]
    shape: str


# ----- naming helpers (kept small + boring on purpose) ---------------------


def _tbl(rng: Random, *, fq: bool = False) -> str:
    """Generate a base table name. ``fq=True`` produces ``schema.name``.

    Keep names sqlglot-friendly (lowercase alpha + underscore). We've seen
    enough oddities with case-sensitive identifiers that we won't fuzz
    quoting here — that's a separate concern from the parser's column
    walker.
    """
    name = rng.choice(["orders", "users", "customers", "events", "products", "items"])
    suffix = rng.randint(0, 999)
    base = f"{name}_{suffix}"
    if fq:
        return f"{rng.choice(['public', 'analytics', 'staging'])}.{base}"
    return base


def _col(rng: Random) -> str:
    """A column name. Bias toward realistic identifiers."""
    return rng.choice(
        [
            "id",
            "user_id",
            "email",
            "amount",
            "total",
            "qty",
            "created_at",
            "status",
            "name",
            "country",
            "score",
            "tier",
        ]
    )


def _alias(rng: Random) -> str:
    return f"a{rng.randint(0, 999):03d}"


def _unique_cols(rng: Random, n: int) -> list[str]:
    """``n`` distinct column names. Distinctness matters because lineage
    keys on column name — duplicates would collapse the expected map."""
    pool = [
        "id",
        "user_id",
        "email",
        "amount",
        "total",
        "qty",
        "created_at",
        "status",
        "name",
        "country",
        "score",
        "tier",
        "kind",
        "src",
        "dst",
    ]
    rng.shuffle(pool)
    return pool[:n]


# ----- L1: baseline projections --------------------------------------------


def gen_simple(rng: Random) -> GeneratedQuery:
    """``SELECT c1, c2, ... FROM t`` — exercises the path that already
    worked in v1. Mostly a regression guard."""
    table = _tbl(rng)
    cols = _unique_cols(rng, rng.randint(1, 5))
    sql = f"SELECT {', '.join(cols)} FROM {table}"
    expected = {c: {(table, c)} for c in cols}
    return GeneratedQuery(sql, expected, "simple")


def gen_alias(rng: Random) -> GeneratedQuery:
    """``SELECT c AS x FROM t`` — alias under the hood is the same lineage
    edge keyed on a different output name."""
    table = _tbl(rng)
    cols = _unique_cols(rng, rng.randint(1, 4))
    aliases = [f"{c}_out_{i}" for i, c in enumerate(cols)]
    parts = ", ".join(f"{c} AS {a}" for c, a in zip(cols, aliases, strict=True))
    sql = f"SELECT {parts} FROM {table}"
    expected = {a: {(table, c)} for c, a in zip(cols, aliases, strict=True)}
    return GeneratedQuery(sql, expected, "alias")


def gen_function(rng: Random) -> GeneratedQuery:
    """``SELECT UPPER(name) AS x, LOWER(email) AS y FROM t`` — function
    wrappers shouldn't hide the underlying column."""
    table = _tbl(rng)
    fns = ["UPPER", "LOWER", "TRIM", "ABS", "ROUND"]
    cols = _unique_cols(rng, rng.randint(1, 4))
    parts: list[str] = []
    expected: dict[str, set[tuple[str, str]]] = {}
    for i, c in enumerate(cols):
        fn = rng.choice(fns)
        out = f"v_{i}"
        parts.append(f"{fn}({c}) AS {out}")
        expected[out] = {(table, c)}
    sql = f"SELECT {', '.join(parts)} FROM {table}"
    return GeneratedQuery(sql, expected, "function")


def gen_arithmetic(rng: Random) -> GeneratedQuery:
    """``SELECT a + b AS sum, a * c AS prod FROM t`` — arithmetic must
    keep every operand as an upstream."""
    table = _tbl(rng)
    cols = _unique_cols(rng, rng.randint(2, 4))
    ops = ["+", "-", "*", "/"]
    parts: list[str] = []
    expected: dict[str, set[tuple[str, str]]] = {}
    for i in range(rng.randint(1, 3)):
        a, b = rng.sample(cols, 2)
        out = f"x_{i}"
        op = rng.choice(ops)
        parts.append(f"{a} {op} {b} AS {out}")
        expected[out] = {(table, a), (table, b)}
    sql = f"SELECT {', '.join(parts)} FROM {table}"
    return GeneratedQuery(sql, expected, "arithmetic")


# ----- L2: predicates / grouping (shape doesn't change lineage) ------------


def gen_with_where(rng: Random) -> GeneratedQuery:
    """WHERE clause filters rows but doesn't touch the projection lineage."""
    table = _tbl(rng)
    cols = _unique_cols(rng, rng.randint(2, 4))
    filter_col = rng.choice(cols)
    sql = f"SELECT {', '.join(cols)} FROM {table} WHERE {filter_col} > {rng.randint(0, 100)}"
    expected = {c: {(table, c)} for c in cols}
    return GeneratedQuery(sql, expected, "where")


def gen_group_by(rng: Random) -> GeneratedQuery:
    """GROUP BY with COUNT(*) — ``COUNT(*)`` should attribute to the base
    table via the aggregate fallback, while grouped columns trace normally."""
    table = _tbl(rng)
    grp = _col(rng)
    sql = f"SELECT {grp}, COUNT(*) AS cnt FROM {table} GROUP BY {grp}"
    expected: dict[str, set[tuple[str, str]]] = {
        grp: {(table, grp)},
        # COUNT(*) is aggregate-only — fallback maps to the scanned table.
        # The "column" name there is "*" in our model, captured by sqlglot
        # as the literal Star expression's column name.
        "cnt": {(table, "*")},
    }
    return GeneratedQuery(sql, expected, "group_by")


def gen_having(rng: Random) -> GeneratedQuery:
    table = _tbl(rng)
    grp = _col(rng)
    sql = (
        f"SELECT {grp}, COUNT(*) AS cnt FROM {table} "
        f"GROUP BY {grp} HAVING COUNT(*) > {rng.randint(1, 10)}"
    )
    expected: dict[str, set[tuple[str, str]]] = {
        grp: {(table, grp)},
        "cnt": {(table, "*")},
    }
    return GeneratedQuery(sql, expected, "having")


def gen_order_limit(rng: Random) -> GeneratedQuery:
    table = _tbl(rng)
    cols = _unique_cols(rng, rng.randint(2, 4))
    sort_col = rng.choice(cols)
    sql = (
        f"SELECT {', '.join(cols)} FROM {table} "
        f"ORDER BY {sort_col} DESC LIMIT {rng.randint(1, 100)}"
    )
    expected = {c: {(table, c)} for c in cols}
    return GeneratedQuery(sql, expected, "order_limit")


# ----- L3: joins -----------------------------------------------------------


def gen_inner_join(rng: Random) -> GeneratedQuery:
    """``SELECT a.x, b.y FROM a JOIN b ON a.id = b.id`` — each projection
    must attribute to its qualifier's table."""
    t1, t2 = _tbl(rng), _tbl(rng)
    while t2 == t1:
        t2 = _tbl(rng)
    cols1 = _unique_cols(rng, rng.randint(1, 3))
    cols2 = _unique_cols(rng, rng.randint(1, 3))
    projections: list[str] = []
    expected: dict[str, set[tuple[str, str]]] = {}
    for c in cols1:
        out = f"l_{c}"
        projections.append(f"a.{c} AS {out}")
        expected[out] = {(t1, c)}
    for c in cols2:
        out = f"r_{c}"
        projections.append(f"b.{c} AS {out}")
        expected[out] = {(t2, c)}
    sql = f"SELECT {', '.join(projections)} FROM {t1} a " f"JOIN {t2} b ON a.id = b.id"
    return GeneratedQuery(sql, expected, "inner_join")


def gen_left_join_coalesce(rng: Random) -> GeneratedQuery:
    """LEFT JOIN with ``COALESCE(a.x, b.y)`` — the canonical multi-source
    output column."""
    t1, t2 = _tbl(rng), _tbl(rng)
    while t2 == t1:
        t2 = _tbl(rng)
    c1, c2 = _unique_cols(rng, 2)
    out = "merged"
    sql = (
        f"SELECT COALESCE(a.{c1}, b.{c2}) AS {out} " f"FROM {t1} a LEFT JOIN {t2} b ON a.id = b.id"
    )
    expected: dict[str, set[tuple[str, str]]] = {
        out: {(t1, c1), (t2, c2)},
    }
    return GeneratedQuery(sql, expected, "left_join_coalesce")


def gen_three_way_join(rng: Random) -> GeneratedQuery:
    tables = [_tbl(rng) for _ in range(3)]
    # ensure unique
    while len(set(tables)) != 3:
        tables = [_tbl(rng) for _ in range(3)]
    t1, t2, t3 = tables
    c1, c2, c3 = _unique_cols(rng, 3)
    sql = (
        f"SELECT a.{c1} AS x, b.{c2} AS y, c.{c3} AS z "
        f"FROM {t1} a "
        f"JOIN {t2} b ON b.fk = a.id "
        f"JOIN {t3} c ON c.fk = b.id"
    )
    expected: dict[str, set[tuple[str, str]]] = {
        "x": {(t1, c1)},
        "y": {(t2, c2)},
        "z": {(t3, c3)},
    }
    return GeneratedQuery(sql, expected, "three_way_join")


# ----- L4: CTEs ------------------------------------------------------------


def gen_single_cte(rng: Random) -> GeneratedQuery:
    """``WITH c AS (SELECT x FROM raw) SELECT x FROM c`` — CTE inlined,
    output traces to ``raw``."""
    base = _tbl(rng)
    cols = _unique_cols(rng, rng.randint(1, 3))
    proj = ", ".join(cols)
    sql = f"WITH c AS (SELECT {proj} FROM {base}) SELECT {proj} FROM c"
    expected = {c: {(base, c)} for c in cols}
    return GeneratedQuery(sql, expected, "single_cte")


def gen_chained_cte(rng: Random) -> GeneratedQuery:
    """``WITH a AS (… raw), b AS (… a), … SELECT FROM last`` — every
    chain depth must terminate at ``raw``. Variable depth (2-6)."""
    base = _tbl(rng)
    cols = _unique_cols(rng, rng.randint(1, 3))
    proj = ", ".join(cols)
    depth = rng.randint(2, 6)
    layers = [f"layer_0 AS (SELECT {proj} FROM {base})"]
    for i in range(1, depth):
        layers.append(f"layer_{i} AS (SELECT {proj} FROM layer_{i - 1})")
    sql = "WITH " + ", ".join(layers) + f" SELECT {proj} FROM layer_{depth - 1}"
    expected = {c: {(base, c)} for c in cols}
    return GeneratedQuery(sql, expected, "chained_cte")


# ----- L5: subqueries ------------------------------------------------------


def gen_subquery_from(rng: Random) -> GeneratedQuery:
    """``SELECT s.x FROM (SELECT c AS x FROM t) s`` — derived-table aliasing."""
    base = _tbl(rng)
    c, alias = _unique_cols(rng, 2)
    sql = f"SELECT s.{alias} FROM (SELECT {c} AS {alias} FROM {base}) s"
    expected: dict[str, set[tuple[str, str]]] = {alias: {(base, c)}}
    return GeneratedQuery(sql, expected, "subquery_from")


def gen_correlated_subquery(rng: Random) -> GeneratedQuery:
    """Correlated scalar subquery — alias-disambiguated like real warehouse
    SQL. Traces to both outer + inner tables."""
    outer = _tbl(rng)
    inner = _tbl(rng)
    while inner == outer:
        inner = _tbl(rng)
    c_out, c_in = _unique_cols(rng, 2)
    sql = (
        f"SELECT u.{c_out}, "
        f"(SELECT MAX(o.{c_in}) FROM {inner} o WHERE o.fk = u.id) AS m "
        f"FROM {outer} u"
    )
    expected: dict[str, set[tuple[str, str]]] = {
        c_out: {(outer, c_out)},
        # ``m`` traces to inner.c_in; some sqlglot versions also include the
        # correlated outer column, which is also legitimate. We assert
        # containment, not equality, in the fuzz test.
        "m": {(inner, c_in)},
    }
    return GeneratedQuery(sql, expected, "correlated_subquery")


# ----- L6: set ops ---------------------------------------------------------


def gen_union(rng: Random) -> GeneratedQuery:
    t1, t2 = _tbl(rng), _tbl(rng)
    while t2 == t1:
        t2 = _tbl(rng)
    c = _col(rng)
    op = rng.choice(["UNION", "UNION ALL"])
    sql = f"SELECT {c} FROM {t1} {op} SELECT {c} FROM {t2}"
    expected: dict[str, set[tuple[str, str]]] = {c: {(t1, c), (t2, c)}}
    return GeneratedQuery(sql, expected, "union")


def gen_union_mismatched_cols(rng: Random) -> GeneratedQuery:
    """UNION whose two branches name different source columns under the
    same output alias — both sources must land in the output set."""
    t1, t2 = _tbl(rng), _tbl(rng)
    while t2 == t1:
        t2 = _tbl(rng)
    c1, c2 = _unique_cols(rng, 2)
    sql = f"SELECT {c1} AS i FROM {t1} UNION ALL SELECT {c2} AS i FROM {t2}"
    expected: dict[str, set[tuple[str, str]]] = {"i": {(t1, c1), (t2, c2)}}
    return GeneratedQuery(sql, expected, "union_mismatched")


# ----- L7: window functions ------------------------------------------------


def gen_window_partition(rng: Random) -> GeneratedQuery:
    """``SUM(amount) OVER (PARTITION BY customer)`` — both the value and
    partition columns feed the windowed output."""
    table = _tbl(rng)
    val, part = _unique_cols(rng, 2)
    fn = rng.choice(["SUM", "AVG", "MAX", "MIN"])
    sql = f"SELECT {part}, {fn}({val}) OVER (PARTITION BY {part}) AS w FROM {table}"
    expected: dict[str, set[tuple[str, str]]] = {
        part: {(table, part)},
        "w": {(table, val), (table, part)},
    }
    return GeneratedQuery(sql, expected, "window_partition")


def gen_row_number(rng: Random) -> GeneratedQuery:
    """``ROW_NUMBER() OVER (PARTITION BY x ORDER BY y)`` — both x and y
    are dependencies of the row-number output."""
    table = _tbl(rng)
    part, order, kept = _unique_cols(rng, 3)
    sql = (
        f"SELECT {kept}, "
        f"ROW_NUMBER() OVER (PARTITION BY {part} ORDER BY {order} DESC) AS rn "
        f"FROM {table}"
    )
    expected: dict[str, set[tuple[str, str]]] = {
        kept: {(table, kept)},
        "rn": {(table, part), (table, order)},
    }
    return GeneratedQuery(sql, expected, "row_number")


# ----- L8: conditional -----------------------------------------------------


def gen_case_when(rng: Random) -> GeneratedQuery:
    """CASE WHEN flag THEN a ELSE b END — every referenced column must
    appear in the output's upstream set."""
    table = _tbl(rng)
    flag, a, b = _unique_cols(rng, 3)
    sql = f"SELECT CASE WHEN {flag} > 0 THEN {a} ELSE {b} END AS v FROM {table}"
    expected: dict[str, set[tuple[str, str]]] = {
        "v": {(table, flag), (table, a), (table, b)},
    }
    return GeneratedQuery(sql, expected, "case_when")


def gen_coalesce_chain(rng: Random) -> GeneratedQuery:
    table = _tbl(rng)
    cols = _unique_cols(rng, rng.randint(2, 4))
    sql = f"SELECT COALESCE({', '.join(cols)}) AS v FROM {table}"
    expected: dict[str, set[tuple[str, str]]] = {"v": {(table, c) for c in cols}}
    return GeneratedQuery(sql, expected, "coalesce_chain")


# ----- L9: combined --------------------------------------------------------


def gen_combined_real_world(rng: Random) -> GeneratedQuery:
    """A staging→marts shaped query that combines CTE + JOIN + window +
    CASE + aggregate. This is the bar we promised the user: realistic
    warehouse SQL that *must* trace cleanly to base tables."""
    customers = _tbl(rng)
    orders = _tbl(rng)
    while orders == customers:
        orders = _tbl(rng)
    sql = f"""
    WITH ranked AS (
        SELECT o.fk AS cid, o.amount,
               ROW_NUMBER() OVER (PARTITION BY o.fk ORDER BY o.amount DESC) AS rk
        FROM {orders} o
    )
    SELECT c.id, c.email,
           COALESCE(r.amount, 0) AS top_amount,
           CASE WHEN r.amount > 100 THEN 'big' ELSE 'small' END AS tier
    FROM {customers} c
    LEFT JOIN ranked r ON r.cid = c.id AND r.rk = 1
    """
    expected: dict[str, set[tuple[str, str]]] = {
        "id": {(customers, "id")},
        "email": {(customers, "email")},
        "top_amount": {(orders, "amount")},
        # ``tier`` is built from the CASE WHEN on r.amount — only orders.amount.
        "tier": {(orders, "amount")},
    }
    return GeneratedQuery(sql, expected, "combined_real_world")


# ----- registry + corpus driver -------------------------------------------


GENERATORS: list[tuple[str, callable[[Random], GeneratedQuery]]] = [
    # L1
    ("simple", gen_simple),
    ("alias", gen_alias),
    ("function", gen_function),
    ("arithmetic", gen_arithmetic),
    # L2
    ("where", gen_with_where),
    ("group_by", gen_group_by),
    ("having", gen_having),
    ("order_limit", gen_order_limit),
    # L3
    ("inner_join", gen_inner_join),
    ("left_join_coalesce", gen_left_join_coalesce),
    ("three_way_join", gen_three_way_join),
    # L4
    ("single_cte", gen_single_cte),
    ("chained_cte", gen_chained_cte),
    # L5
    ("subquery_from", gen_subquery_from),
    ("correlated_subquery", gen_correlated_subquery),
    # L6
    ("union", gen_union),
    ("union_mismatched", gen_union_mismatched_cols),
    # L7
    ("window_partition", gen_window_partition),
    ("row_number", gen_row_number),
    # L8
    ("case_when", gen_case_when),
    ("coalesce_chain", gen_coalesce_chain),
    # L9
    ("combined_real_world", gen_combined_real_world),
]


def generate_corpus(*, total: int = 2000, seed: int = 0) -> Iterator[GeneratedQuery]:
    """Yield ``total`` queries split evenly across all generators.

    The split is round-robin (rather than category-blocked) so a fuzz run
    that bails on first failure surfaces issues across the whole spectrum
    rather than only the first category.
    """
    rng = Random(seed)
    per = total // len(GENERATORS)
    remainder = total - per * len(GENERATORS)
    for i in range(per + (1 if remainder else 0)):
        for _, fn in GENERATORS:
            if i >= per and remainder <= 0:
                break
            yield fn(rng)
        if i >= per:
            remainder -= len(GENERATORS)
