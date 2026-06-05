"""Cross-dialect migration matrix lock-in (Phase AGO).

The web advertises a 10x10 cross-DB migration matrix. The per-dialect
tests in ``test_type_mapping.py`` check each dialect's canonical→DDL
table in isolation; this file pins the *cross product* — i.e. the actual
migration operation ``translate(source_vendor_type, target_dialect)`` —
for the most migration-relevant column types across every SQL-capable
dialect.

Why hardcode the expected DDL rather than compute it? So a change to one
dialect's mapping (or the shared render logic, e.g. the DECIMAL-precision
or VARCHAR-length rules) is caught as an explicit, reviewed diff here
instead of silently altering what a cross-DB migration emits.
"""

from __future__ import annotations

import pytest

from etl_plugins.core.type_mapping import _DIALECT_DDL, CanonicalType, translate

# Every SQL-capable dialect that is a migration target (SchemaWriter).
# (DynamoDB/Kinesis/SQS/Redis are not here — they don't emit DDL.)
SQL_DIALECTS = [
    "sqlite",
    "postgres",
    "mysql",
    "vertica",
    "mssql",
    "snowflake",
    "bigquery",
    "redshift",
    "clickhouse",
    "cassandra",
]

# source vendor type -> {dialect: expected rendered DDL}
MATRIX: dict[str, dict[str, str]] = {
    "BIGINT": {
        "sqlite": "INTEGER",
        "postgres": "BIGINT",
        "mysql": "BIGINT",
        "vertica": "BIGINT",
        "mssql": "BIGINT",
        "snowflake": "BIGINT",
        "bigquery": "INT64",
        "redshift": "BIGINT",
        "clickhouse": "Int64",
        "cassandra": "bigint",
    },
    "VARCHAR(255)": {
        "sqlite": "TEXT",
        "postgres": "VARCHAR(255)",
        "mysql": "VARCHAR(255)",
        "vertica": "VARCHAR(255)",
        "mssql": "NVARCHAR(255)",
        "snowflake": "VARCHAR(255)",
        "bigquery": "STRING",
        "redshift": "VARCHAR(255)",
        "clickhouse": "String",
        "cassandra": "text",
    },
    "TIMESTAMPTZ": {
        "sqlite": "TEXT",
        "postgres": "TIMESTAMPTZ",
        "mysql": "DATETIME",
        "vertica": "TIMESTAMPTZ",
        "mssql": "DATETIME2",
        "snowflake": "TIMESTAMP_TZ",
        "bigquery": "TIMESTAMP",
        "redshift": "TIMESTAMPTZ",
        "clickhouse": "DateTime64(3)",
        "cassandra": "timestamp",
    },
    "JSON": {
        "sqlite": "TEXT",
        "postgres": "JSONB",
        "mysql": "JSON",
        "vertica": "LONG VARCHAR",
        "mssql": "NVARCHAR(MAX)",
        "snowflake": "VARIANT",
        "bigquery": "JSON",
        "redshift": "SUPER",
        "clickhouse": "String",
        "cassandra": "text",
    },
    "DECIMAL(10,2)": {
        "sqlite": "NUMERIC(10,2)",
        "postgres": "NUMERIC(10,2)",
        "mysql": "DECIMAL(10,2)",
        "vertica": "NUMERIC(10,2)",
        "mssql": "DECIMAL(10,2)",
        "snowflake": "NUMBER(10,2)",
        "bigquery": "NUMERIC(10,2)",
        "redshift": "DECIMAL(10,2)",
        "clickhouse": "Decimal(10,2)",
        "cassandra": "decimal",  # arbitrary-precision — no (p,s)
    },
    "BOOLEAN": {
        "sqlite": "INTEGER",
        "postgres": "BOOLEAN",
        "mysql": "TINYINT(1)",
        "vertica": "BOOLEAN",
        "mssql": "BIT",
        "snowflake": "BOOLEAN",
        "bigquery": "BOOL",
        "redshift": "BOOLEAN",
        "clickhouse": "Bool",
        "cassandra": "boolean",
    },
    "BYTEA": {
        "sqlite": "BLOB",
        "postgres": "BYTEA",
        "mysql": "BLOB",
        "vertica": "VARBINARY",
        "mssql": "VARBINARY(MAX)",
        "snowflake": "BINARY",
        "bigquery": "BYTES",
        "redshift": "VARBYTE",
        "clickhouse": "String",
        "cassandra": "blob",
    },
}


@pytest.mark.parametrize(
    "source_type, dialect, expected",
    [(src, dialect, expected) for src, row in MATRIX.items() for dialect, expected in row.items()],
)
def test_migration_matrix(source_type: str, dialect: str, expected: str) -> None:
    assert translate(source_type, target_dialect=dialect) == expected


def test_matrix_covers_every_sql_dialect() -> None:
    """Each matrix row must cover all SQL dialects — so adding a dialect
    without extending the matrix fails loudly."""
    for src, row in MATRIX.items():
        assert set(row) == set(SQL_DIALECTS), f"{src} row missing dialects"


def test_every_sql_dialect_renders_every_canonical_type() -> None:
    """Totality guard: every SQL dialect must map every CanonicalType to a
    non-empty DDL fragment (a missing entry would KeyError at render time)."""
    for dialect in SQL_DIALECTS:
        table = _DIALECT_DDL[dialect]
        for canonical in CanonicalType:
            assert table[canonical], f"{dialect} missing {canonical}"
