"""SQL column-level lineage extractor (ADR-0041 Phase X, 2026-05-28).

The previous :mod:`etl_plugins.runtime.column_lineage` SQL helper hand-walked the
``sqlglot`` AST and gave up on anything more interesting than ``SELECT a, b FROM
t`` — joins, CTEs, subqueries, UNIONs all collapsed to "opaque sink". That left
the catalog with no column lineage for the warehouse-to-warehouse pipelines
that *actually* needed it (most real-world cases).

This module replaces that with :func:`extract_sql_lineage`, which uses
``sqlglot.lineage.lineage`` to walk every output column back to its leaf-table
origins. The library already understands:

* ``WITH`` / CTE chains, including recursive ones
* nested derived tables (``SELECT … FROM (SELECT …) sub``)
* ``JOIN`` of any kind — leaves preserve the originating table
* ``UNION [ALL]`` — each branch contributes leaves
* ``COALESCE`` / ``CASE WHEN`` / arithmetic — every referenced column shows up
* window functions — the ``PARTITION BY`` / ``ORDER BY`` columns count as deps
* ``LATERAL`` joins (Postgres dialect)

We map each leaf back to ``(table_name, column_name)``. The caller in
:mod:`etl_plugins.runtime.column_lineage` glues this to the pipeline's source
``connection`` to produce :class:`ColumnRef` upstreams — a single output column
can have many (a 1→N edge, which the :class:`ColumnEdge` data model has always
supported via ``tuple[ColumnRef, ...]``).

``SELECT *`` without a ``schema`` is still opaque (we can't enumerate columns
the parser doesn't see). Callers can provide a schema dict (same format as
``sqlglot.optimizer.qualify``) to expand stars at lineage-derivation time —
v2 leaves that hook in but the runtime worker doesn't wire schema yet (a
later slice can grab it via the connector's :class:`SchemaInspector`).
"""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.lineage import Node as _LineageNode
from sqlglot.lineage import lineage as _sqlglot_lineage


def extract_sql_lineage(
    query: str,
    *,
    dialect: str | None = None,
    schema: dict[str, Any] | None = None,
) -> dict[str, list[tuple[str, str]]] | None:
    """Resolve a SELECT statement's column lineage.

    Returns a mapping ``{output_column: [(source_table, source_column), ...]}``
    where each value lists every leaf-table origin that contributes to the
    output column. Multiple entries are normal — e.g. ``COALESCE(a.x, b.y)``
    yields ``[("a", "x"), ("b", "y")]``. An empty list means the column exists
    but its origin couldn't be resolved (e.g. a literal, an unresolved
    reference). A top-level ``None`` means we should not even try (not a
    SELECT, ``SELECT *`` without schema, or sqlglot couldn't parse the query).

    Args:
        query: The raw SQL text. Must be a single statement.
        dialect: Optional sqlglot dialect hint (``"postgres"`` / ``"mysql"`` /
            ``"snowflake"`` …). Passing the right dialect helps with vendor
            extensions like ``LATERAL`` and ``QUALIFY``.
        schema: Optional schema dictionary in ``sqlglot.optimizer.qualify``
            format (``{table: {column: type}}``) — when provided, ``SELECT *``
            is expanded before lineage runs so star projections resolve too.
    """
    try:
        # ``sqlglot.parse_one`` is typed as returning ``exp.Expr`` (the
        # implicit-string superclass); the body of this module pins it to
        # ``Expression`` so the structural ``isinstance`` checks below
        # narrow as expected. It can return ``None`` for empty input in
        # practice even though the stubs claim non-optional.
        parsed_any = sqlglot.parse_one(query, dialect=dialect)
    except Exception:
        return None
    if parsed_any is None or not isinstance(parsed_any, exp.Expression):
        return None
    parsed: exp.Expression = parsed_any

    # We accept SELECT, set ops (UNION/INTERSECT/EXCEPT), and a top-level CTE
    # whose body is a SELECT. Everything else (INSERT, CREATE TABLE AS, raw
    # DDL) we leave opaque — that's the worker's data-plane concern, not the
    # static catalog's.
    body: exp.Expression = parsed.expression if isinstance(parsed, exp.With) else parsed
    if not isinstance(body, exp.Select | exp.Union):
        return None

    # Optionally qualify ``*`` projections so they show up in
    # ``select.expressions`` with real column names.
    work_sql = query
    if schema is not None:
        try:
            from sqlglot.optimizer.qualify import qualify

            qualified = qualify(parsed.copy(), schema=schema, dialect=dialect)
            work_sql = qualified.sql(dialect=dialect)
            parsed = qualified
            body = qualified.expression if isinstance(qualified, exp.With) else qualified
        except Exception:
            # Best-effort. If qualify fails (bad schema, dialect mismatch),
            # fall back to the raw query; star projections will remain opaque.
            pass

    # The output column list for a UNION takes its names from the first
    # SELECT branch (SQL semantics). Drill into the leftmost SELECT to get
    # alias_or_name values; lineage() itself handles the union traversal.
    select_for_columns: Any = body
    while isinstance(select_for_columns, exp.Union):
        # ``exp.Union.left`` returns the ``Query`` superclass — narrowing via
        # the runtime ``isinstance`` on the next iteration. We pin the variable
        # to ``Any`` so the recursion typechecks both with and without the
        # sqlglot type stubs installed (pre-commit's mypy lacks them).
        select_for_columns = select_for_columns.left
    if not isinstance(select_for_columns, exp.Select):
        return None

    # ``SELECT *`` (or qualified ``t.*``) we can't enumerate without a schema.
    for proj in select_for_columns.expressions:
        if isinstance(proj, exp.Star) or (
            isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star)
        ):
            return None

    # Build a global alias → real-table map up front so we can recover
    # lineage from Placeholder leaves (correlated/lateral references that
    # ``sqlglot.lineage`` can't resolve through its scope walker).
    alias_to_table = _build_alias_table_map(parsed)

    result: dict[str, list[tuple[str, str]]] = {}
    for proj in select_for_columns.expressions:
        col_name = proj.alias_or_name
        if not col_name:
            # An unaliased complex expression (e.g. ``a + 1``) — sqlglot uses
            # the expression's SQL as ``name``. We still record it under that
            # synthetic name so the column count matches the output.
            col_name = proj.sql()
        try:
            node = _sqlglot_lineage(col_name, sql=work_sql, dialect=dialect, schema=schema)
        except Exception:
            result[col_name] = []
            continue
        result[col_name] = _collect_leaves(node, alias_to_table)
    return result


def _collect_leaves(
    root: _LineageNode,
    alias_to_table: dict[str, str],
) -> list[tuple[str, str]]:
    """Walk the lineage tree, returning ``[(table, column), ...]`` for every
    leaf (a node with no ``downstream`` children). Deduplicates while
    preserving first-seen order. Drops leaves where neither the source
    expression nor the ``alias_to_table`` map can resolve the table — the
    caller treats a column with all-empty leaves as "exists, origin unknown".

    The alias map is the fallback path that handles two cases ``sqlglot``
    doesn't trace cleanly:

    * **Correlated subqueries / lateral joins** — leaves come through as
      ``Placeholder`` source with names like ``"o.amount"``. We split the
      qualifier and look up ``"o" → "orders"``.
    * **Aliased CTE/subquery refs that bottom out at a base table alias** —
      same recovery.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    stack: list[_LineageNode] = [root]
    while stack:
        n = stack.pop()
        if not n.downstream:
            tbl, col = _resolve_leaf(n, alias_to_table)
            if tbl and col:
                key = (tbl, col)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
            continue
        # Reverse to preserve left-to-right child order under LIFO stack.
        for d in reversed(n.downstream):
            stack.append(d)
    return out


def _resolve_leaf(
    node: _LineageNode,
    alias_to_table: dict[str, str],
) -> tuple[str | None, str | None]:
    """Best-effort ``(table, column)`` resolution for one lineage leaf.

    Three resolution paths in priority order:

    1. **Direct table source** — the leaf's ``source`` is an ``exp.Table``
       (or aliased Table). Use that table; the leaf's ``name`` already
       carries the qualified column.
    2. **Aggregate fallback** (``source=Select``) — the lineage walker
       bottomed out at the enclosing Select (typical for ``COUNT(*)``).
       If the leaf's expression actually references the scanned table,
       attribute it to that table; use the *real* column reference inside
       the expression for the column name (not the projection alias).
       Pure literals fail this guard so they don't manufacture upstreams.
    3. **Placeholder fallback** — correlated / lateral refs that sqlglot
       couldn't resolve. The leaf's qualifier (``"o"`` in ``"o.amount"``)
       gets looked up in the FROM-aliases map for the whole query.
    """
    # Names can come through quoted (``"t"."a"``) after the qualify pass —
    # strip the SQL identifier quoting before splitting.
    name = node.name.replace('"', "").replace("`", "")
    if "." in name:
        qualifier, col = name.split(".", 1)
    else:
        qualifier, col = None, name
    if not col:
        return None, None

    # (1) Direct table source.
    direct = _table_of_simple(node.source)
    if direct is not None:
        return direct, col

    # (2) Aggregate fallback. The leaf's outer name is the projection
    # alias, which is *not* a real source column — use whatever the inner
    # expression actually references.
    if isinstance(node.source, exp.Select):
        if not _expression_touches_table(node.expression):
            return None, None
        tbl = _single_base_table(node.source)
        if tbl is None:
            return None, None
        return tbl, _column_from_expression(node.expression)

    # (3) Placeholder fallback.
    if qualifier:
        resolved = alias_to_table.get(
            qualifier,
            qualifier if qualifier in alias_to_table.values() else None,
        )
        if resolved:
            return resolved, col
    return None, None


def _column_from_expression(expr: object) -> str:
    """Find the first ``exp.Column`` or ``exp.Star`` reference inside an
    aggregate leaf's expression. ``COUNT(*)`` returns ``"*"``; an aggregate
    like ``COUNT(name)`` returns ``"name"``. Defaulting to ``"*"`` for the
    no-reference case keeps the API total — the caller has already
    checked ``_expression_touches_table`` so we know there's *some* ref."""
    if not isinstance(expr, exp.Expression):
        return "*"
    for n in expr.walk():
        if isinstance(n, exp.Column):
            return str(n.name)
        if isinstance(n, exp.Star):
            return "*"
    return "*"


def _table_of_simple(source: object) -> str | None:
    """Direct Table / aliased Table lookup. No fallback heuristics.

    Returns the fully-qualified name (``catalog.schema.table`` if all parts
    are present, otherwise the shortest dotted form). This matches what
    :func:`derive_asset_key` keys on for table sinks — without it,
    ``SELECT * FROM public.orders`` would key as ``orders`` here but
    ``public.orders`` everywhere else, fanning the catalog into two rows.
    """
    if isinstance(source, exp.Table):
        return _table_fq_name(source)
    if isinstance(source, exp.Alias):
        inner = source.this
        if isinstance(inner, exp.Table):
            return _table_fq_name(inner)
    return None


def _table_fq_name(tbl: exp.Table) -> str | None:
    """Reconstruct ``catalog.db.name`` from a sqlglot ``Table`` node."""
    if not tbl.name:
        return None
    parts = [p for p in (tbl.catalog, tbl.db, tbl.name) if p]
    return ".".join(parts) if parts else None


def _expression_touches_table(expr: object) -> bool:
    """``True`` when the leaf's projection expression actually references
    table data — a ``Column`` reference or a ``Star`` (``COUNT(*)``). Pure
    literal / parameter expressions return ``False`` so we don't fabricate
    a fake dependency on the scanned table."""
    if not isinstance(expr, exp.Expression):
        return False
    return any(isinstance(n, exp.Column | exp.Star) for n in expr.walk())


def _single_base_table(select: exp.Select) -> str | None:
    """If a ``Select`` reads from exactly one base table (ignoring CTEs in
    the enclosing scope), return its fully-qualified name; otherwise
    ``None``. Used to attribute aggregate leaves to the table they scan."""
    names: list[str] = []
    for tbl in select.find_all(exp.Table):
        if not tbl.name or _is_cte_ref(tbl, select):
            continue
        fq = _table_fq_name(tbl)
        if fq:
            names.append(fq)
    unique = list(dict.fromkeys(names))
    if len(unique) == 1:
        return unique[0]
    return None


def _build_alias_table_map(root: exp.Expression) -> dict[str, str]:
    """Walk every ``Table`` node and record ``alias → real-table-name``.

    Includes identity entries (``real_name → real_name``) so a qualifier
    that's already a table name resolves cleanly. CTE names are skipped —
    the lineage walker has already inlined them by the time we collect
    leaves, so the meaningful endpoints are the base tables.
    """
    cte_names = {cte.alias_or_name for cte in root.find_all(exp.CTE) if cte.alias_or_name}
    mapping: dict[str, str] = {}
    for tbl in root.find_all(exp.Table):
        name = tbl.name
        if not name or name in cte_names:
            continue
        fq = _table_fq_name(tbl) or name
        # Identity entries cover both the bare name and the fully-qualified
        # form so a Placeholder leaf's qualifier resolves regardless of the
        # form the writer used.
        mapping[name] = fq
        mapping[fq] = fq
        alias = tbl.alias
        if alias and alias != name:
            mapping[alias] = fq
    return mapping


def _is_cte_ref(table: exp.Table, scope: exp.Expression) -> bool:
    """A ``Table`` node whose name matches a CTE defined in the enclosing
    scope is a CTE reference, not a base table — ignore it when picking the
    fallback table for an aggregate leaf."""
    # ``exp.Expression.parent`` is the broader ``Expr | None`` in sqlglot's
    # own stubs but ``Any`` without them — declare ``Any`` so both
    # configurations typecheck. Each iteration validates via ``isinstance``.
    parent: Any = scope
    seen: set[str] = set()
    while parent is not None:
        if isinstance(parent, exp.With):
            for cte in parent.expressions:
                if isinstance(cte, exp.CTE) and cte.alias_or_name:
                    seen.add(cte.alias_or_name)
        parent = parent.parent
    return table.name in seen


def extract_referenced_tables(query: str, *, dialect: str | None = None) -> list[str]:
    """List every *base* table the query references, in first-seen order.

    CTE definitions are excluded — they're internal aliases for the query
    itself, not asset-level inputs. Sub-selects, JOINs, UNIONs, correlated
    subqueries and lateral joins all contribute their leaf-table references.

    Returns an empty list for non-SQL strings (a Mongo collection name handed
    in as ``source.query``, for instance), un-parseable input, or anything
    that's not a parsed sqlglot expression. The caller treats "no referenced
    tables" as "trust the primary key derivation" — no auto-fanout.

    This is the asset-axis companion to :func:`extract_sql_lineage`: where
    that one walks down to leaf *columns*, this one walks down to leaf
    *tables*. The static lineage emitter uses it to register every joined /
    union'd / sub-queried base table as an input asset, so the catalog graph
    matches the column-edge graph rather than only the single FROM table
    that the regex-based ``derive_asset_key`` picks up.
    """
    try:
        parsed = sqlglot.parse_one(query, dialect=dialect)
    except Exception:
        return []
    if not isinstance(parsed, exp.Expression):
        return []
    cte_names = {cte.alias_or_name for cte in parsed.find_all(exp.CTE) if cte.alias_or_name}
    out: list[str] = []
    seen: set[str] = set()
    for tbl in parsed.find_all(exp.Table):
        name = tbl.name
        if not name or name in cte_names:
            continue
        fq = _table_fq_name(tbl) or name
        if fq in seen:
            continue
        seen.add(fq)
        out.append(fq)
    return out


__all__ = ["extract_referenced_tables", "extract_sql_lineage"]
