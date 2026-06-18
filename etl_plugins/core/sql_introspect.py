"""Core-level SQL table-reference extraction (sqlglot).

Lives in ``core`` — not ``runtime`` — so the cross-DB auto-create-table
path (:meth:`etl_plugins.core.pipeline.Pipeline._auto_create_sink_tables`)
can find a query's base table without ``core`` importing the ``runtime``
layer (ADR-0017 / ADR-0022 단방향 의존; the import-linter contract
``core-must-not-import-upper-layers``).

The richer column-level lineage walker stays in
:mod:`etl_plugins.runtime.sql_lineage`, which re-exports
:func:`extract_referenced_tables` (and reuses :func:`_table_fq_name`) so
existing importers there are unaffected.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def _table_fq_name(tbl: exp.Table) -> str | None:
    """Reconstruct ``catalog.db.name`` from a sqlglot ``Table`` node."""
    if not tbl.name:
        return None
    parts = [p for p in (tbl.catalog, tbl.db, tbl.name) if p]
    return ".".join(parts) if parts else None


def extract_referenced_tables(query: str, *, dialect: str | None = None) -> list[str]:
    """List every *base* table the query references, in first-seen order.

    CTE definitions are excluded — they're internal aliases for the query
    itself, not asset-level inputs. Sub-selects, JOINs, UNIONs, correlated
    subqueries and lateral joins all contribute their leaf-table references.

    Returns an empty list for non-SQL strings (a Mongo collection name handed
    in as ``source.query``, for instance), un-parseable input, or anything
    that's not a parsed sqlglot expression. The caller treats "no referenced
    tables" as "trust the primary key derivation" — no auto-fanout.

    This is the asset-axis companion to
    :func:`etl_plugins.runtime.sql_lineage.extract_sql_lineage`: where that
    one walks down to leaf *columns*, this one walks down to leaf *tables*.
    The static lineage emitter uses it to register every joined / union'd /
    sub-queried base table as an input asset, so the catalog graph matches
    the column-edge graph rather than only the single FROM table that the
    regex-based ``derive_asset_key`` picks up.
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


_WRITE_STATEMENTS = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create)


def extract_statement_io(
    statement: str, *, dialect: str | None = None
) -> tuple[list[str], list[str]]:
    """``(write_targets, read_sources)`` for a DML/DDL statement (ADR-0099).

    A ``sql`` operator (Operator DAG) runs arbitrary statements; for catalog
    lineage we need the table it *writes* (INSERT / UPDATE / DELETE / MERGE /
    CREATE target) and the tables it *reads* (FROM / JOIN / subquery). The
    write target is excluded from the read list. CTE names are excluded.

    Returns ``([], [])`` for a pure ``SELECT`` (no write), a non-SQL string, or
    anything sqlglot can't parse — the caller treats that as "no lineage".
    """
    try:
        parsed = sqlglot.parse_one(statement, dialect=dialect)
    except Exception:
        return [], []
    if not isinstance(parsed, exp.Expression):
        return [], []
    targets: list[str] = []
    target_set: set[str] = set()
    if isinstance(parsed, _WRITE_STATEMENTS):
        this = parsed.this
        tgt = this if isinstance(this, exp.Table) else (this.find(exp.Table) if this else None)
        if tgt is not None:
            fq = _table_fq_name(tgt)
            if fq:
                targets.append(fq)
                target_set.add(fq)
    cte_names = {cte.alias_or_name for cte in parsed.find_all(exp.CTE) if cte.alias_or_name}
    sources: list[str] = []
    seen: set[str] = set()
    for tbl in parsed.find_all(exp.Table):
        name = tbl.name
        if not name or name in cte_names:
            continue
        fq = _table_fq_name(tbl) or name
        if fq in target_set or fq in seen:
            continue
        seen.add(fq)
        sources.append(fq)
    return targets, sources


__all__ = ["extract_referenced_tables", "extract_statement_io"]
