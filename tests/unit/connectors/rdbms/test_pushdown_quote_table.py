"""Per-dialect ``quote_table`` lock-in (ADR-0094 follow-up, 2026-06-12).

Same-connection SQL pushdown builds ``INSERT INTO <quote_table(t)> <select>``
in the target dialect. An unquoted target folds case (postgres lowercases),
so a quoted-uppercase warehouse table fails the moment pushdown engages —
the live-data bug this method fixed. The quoting must also MATCH each
connector's own write-path quoting, or the same config would behave
differently on the Arrow/Record path vs the pushdown path.

These are pure string assertions — no driver, no server. A wrong quote
character per dialect would silently break pushdown only on that dialect,
which no cross-dialect test would otherwise catch.
"""

from __future__ import annotations

import pytest

from etl_plugins.core.registry import ConnectorRegistry

# (connector type, kwargs, expected quote of "MySchema.MyTable")
_CASES = [
    (
        "postgres",
        {"host": "x", "database": "d", "user": "u", "password": "p"},
        '"MySchema"."MyTable"',
    ),
    ("mysql", {"host": "x", "database": "d", "user": "u", "password": "p"}, "`MySchema`.`MyTable`"),
    ("sqlite", {"database": ":memory:"}, '"MySchema"."MyTable"'),
    (
        "vertica",
        {"host": "x", "database": "d", "user": "u", "password": "p"},
        '"MySchema"."MyTable"',
    ),
    ("mssql", {"host": "x", "database": "d", "user": "u", "password": "p"}, "[MySchema].[MyTable]"),
    (
        "snowflake",
        {
            "account": "a",
            "user": "u",
            "password": "p",
            "warehouse": "w",
            "database": "d",
            "schema": "s",
        },
        '"MySchema"."MyTable"',
    ),
    (
        "redshift",
        {"host": "x", "database": "d", "user": "u", "password": "p"},
        '"MySchema"."MyTable"',
    ),
    # BigQuery wraps the whole path in ONE backtick pair (its own convention).
    ("bigquery", {"project": "p", "dataset": "ds"}, "`MySchema.MyTable`"),
    (
        "clickhouse",
        {"host": "x", "database": "d", "user": "u", "password": "p"},
        "`MySchema`.`MyTable`",
    ),
]


@pytest.mark.parametrize("ctype, kwargs, expected", _CASES, ids=[c[0] for c in _CASES])
def test_quote_table_per_dialect(ctype: str, kwargs: dict, expected: str) -> None:
    conn = ConnectorRegistry.get(ctype)(**kwargs)
    assert conn.quote_table("MySchema.MyTable") == expected


@pytest.mark.parametrize("ctype, kwargs, _", _CASES, ids=[c[0] for c in _CASES])
def test_quote_table_bare_name(ctype: str, kwargs: dict, _: str) -> None:
    """A bare (unqualified) table must still quote — BigQuery/ClickHouse
    additionally qualify with their default dataset/database, the rest
    quote as-is. We only assert the name is present and quoted (not
    bare), since the qualifier prefix is dialect-specific."""
    conn = ConnectorRegistry.get(ctype)(**kwargs)
    out = conn.quote_table("MyTable")
    assert "MyTable" in out
    assert out != "MyTable"  # never emit an unquoted identifier
