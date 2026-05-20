"""Translate the no-code filter grammar to a Spark SQL predicate (ADR-0031).

The ``filter`` transform and graph edge ``when`` store a sandboxed Python
expression in the *same* limited grammar the visual builder emits
(``data['col'] <op> <value>`` clauses AND-joined, plus empty/not-empty/contains).
The ``local`` backend ``eval``s it; the Spark backend can't, so it renders the
clauses to Spark SQL instead. Anything outside the grammar (hand-written /
``or`` / function calls) raises — those pipelines must use the ``local`` engine.
"""

from __future__ import annotations

import re

# Single capture group = the dict key (matches lib/filter-expr.ts KEY).
_KEY = r"data\[\s*['\"]([^'\"]+)['\"]\s*\]"
_CMP = {"==": "=", "!=": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<="}


def _literal(raw: str) -> str | None:
    """Render a clause value as a Spark SQL literal. ``None`` = SQL NULL sentinel."""
    s = raw.strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        return s
    if s in ("true", "True"):
        return "true"
    if s in ("false", "False"):
        return "false"
    if s in ("null", "None"):
        return None
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1]
    return "'" + s.replace("'", "''") + "'"


def _clause(p: str) -> str:
    m = re.fullmatch(rf"not\s+{_KEY}", p)
    if m:
        return f"`{m.group(1)}` IS NULL"
    m = re.fullmatch(_KEY, p)
    if m:
        return f"`{m.group(1)}` IS NOT NULL"
    m = re.fullmatch(rf"(.+?)\s+in\s+{_KEY}", p)
    if m:
        lit = _literal(m.group(1))
        inner = lit[1:-1] if isinstance(lit, str) and lit.startswith("'") else str(lit)
        inner = inner.replace("%", r"\%").replace("_", r"\_")
        return f"`{m.group(2)}` LIKE '%{inner}%'"
    m = re.fullmatch(rf"{_KEY}\s*(==|!=|>=|<=|>|<)\s*(.+)", p)
    if m:
        col, op, lit = m.group(1), m.group(2), _literal(m.group(3))
        if lit is None:
            return f"`{col}` IS NULL" if op == "==" else f"`{col}` IS NOT NULL"
        return f"`{col}` {_CMP[op]} {lit}"
    raise ValueError(f"cannot translate predicate to Spark SQL: {p!r}")


def to_spark_sql(expr: str | None) -> str | None:
    """Translate an AND-joined filter expression to Spark SQL, or ``None`` if empty.

    Raises :class:`ValueError` for any clause outside the supported grammar.
    """
    if expr is None or not expr.strip():
        return None
    return " AND ".join(_clause(part.strip()) for part in expr.strip().split(" and "))
