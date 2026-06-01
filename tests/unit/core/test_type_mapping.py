"""DB-agnostic type mapping (Phase VV, ADR-0066, 2026-05-29)."""

from __future__ import annotations

import pytest

from etl_plugins.core.type_mapping import (
    CanonicalType,
    TypeSpec,
    normalize_db_type,
    render_canonical,
    translate,
)

# ---------- normalisation ----------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("INTEGER", CanonicalType.INTEGER),
        ("int", CanonicalType.INTEGER),
        ("INT4", CanonicalType.INTEGER),
        ("smallint", CanonicalType.SMALLINT),
        ("tinyint", CanonicalType.SMALLINT),
        ("BIGINT", CanonicalType.BIGINT),
        ("INT8", CanonicalType.BIGINT),
        ("REAL", CanonicalType.REAL),
        ("FLOAT4", CanonicalType.REAL),
        ("DOUBLE", CanonicalType.DOUBLE),
        ("DOUBLE PRECISION", CanonicalType.DOUBLE),
        ("FLOAT8", CanonicalType.DOUBLE),
        ("TEXT", CanonicalType.TEXT),
        ("LONGTEXT", CanonicalType.TEXT),
        ("BOOLEAN", CanonicalType.BOOLEAN),
        ("BOOL", CanonicalType.BOOLEAN),
        ("TIMESTAMP", CanonicalType.TIMESTAMP),
        ("timestamp with time zone", CanonicalType.TIMESTAMP),
        ("TIMESTAMPTZ", CanonicalType.TIMESTAMP),
        ("DATETIME", CanonicalType.TIMESTAMP),
        ("DATE", CanonicalType.DATE),
        ("JSON", CanonicalType.JSON),
        ("JSONB", CanonicalType.JSON),
        ("BLOB", CanonicalType.BLOB),
        ("BYTEA", CanonicalType.BLOB),
    ],
)
def test_normalize_known_vendor_types(raw: str, expected: CanonicalType) -> None:
    assert normalize_db_type(raw).canonical is expected


def test_normalize_strips_length_for_varchar() -> None:
    spec = normalize_db_type("VARCHAR(255)")
    assert spec.canonical is CanonicalType.VARCHAR
    assert spec.length == 255


def test_normalize_keeps_precision_and_scale_for_decimal() -> None:
    spec = normalize_db_type("DECIMAL(10,2)")
    assert spec.canonical is CanonicalType.DECIMAL
    assert spec.precision == 10
    assert spec.scale == 2


def test_normalize_unknown_type_falls_back_to_text() -> None:
    """Unknown vendor types map to TEXT — the safest default since any
    value round-trips through a text column."""
    assert normalize_db_type("MYSTERY_TYPE").canonical is CanonicalType.TEXT


def test_normalize_empty_string_returns_text() -> None:
    assert normalize_db_type("").canonical is CanonicalType.TEXT


# ---------- rendering ----------


@pytest.mark.parametrize(
    "canonical, dialect, expected",
    [
        (CanonicalType.INTEGER, "sqlite", "INTEGER"),
        (CanonicalType.INTEGER, "postgres", "INTEGER"),
        (CanonicalType.INTEGER, "mysql", "INT"),
        (CanonicalType.BIGINT, "sqlite", "INTEGER"),  # type affinity
        (CanonicalType.BIGINT, "postgres", "BIGINT"),
        (CanonicalType.BIGINT, "mysql", "BIGINT"),
        (CanonicalType.TIMESTAMP, "sqlite", "TEXT"),  # ISO 8601 strings
        (CanonicalType.TIMESTAMP, "postgres", "TIMESTAMPTZ"),
        (CanonicalType.TIMESTAMP, "mysql", "DATETIME"),
        (CanonicalType.JSON, "sqlite", "TEXT"),
        (CanonicalType.JSON, "postgres", "JSONB"),
        (CanonicalType.JSON, "mysql", "JSON"),
        (CanonicalType.BOOLEAN, "sqlite", "INTEGER"),
        (CanonicalType.BOOLEAN, "mysql", "TINYINT(1)"),
        (CanonicalType.DOUBLE, "postgres", "DOUBLE PRECISION"),
    ],
)
def test_render_canonical_dialect_specific(
    canonical: CanonicalType, dialect: str, expected: str
) -> None:
    assert render_canonical(TypeSpec(canonical), dialect=dialect) == expected


def test_render_varchar_keeps_length() -> None:
    assert (
        render_canonical(TypeSpec(CanonicalType.VARCHAR, length=255), dialect="postgres")
        == "VARCHAR(255)"
    )
    # SQLite collapses VARCHAR to TEXT and drops the length — type affinity.
    assert render_canonical(TypeSpec(CanonicalType.VARCHAR, length=255), dialect="sqlite") == "TEXT"


def test_render_decimal_keeps_precision_and_scale() -> None:
    spec = TypeSpec(CanonicalType.DECIMAL, precision=10, scale=2)
    assert render_canonical(spec, dialect="postgres") == "NUMERIC(10,2)"
    assert render_canonical(spec, dialect="mysql") == "DECIMAL(10,2)"
    # SQLite keeps the precision since NUMERIC accepts it.
    assert render_canonical(spec, dialect="sqlite") == "NUMERIC(10,2)"


def test_render_unknown_dialect_falls_back_to_sqlite() -> None:
    """A typo in a connector's dialect tag shouldn't crash — sqlite
    affinity is permissive enough to accept anything."""
    assert render_canonical(TypeSpec(CanonicalType.BIGINT), dialect="snowflake") == "INTEGER"


# ---------- one-call translate ----------


@pytest.mark.parametrize(
    "raw, dialect, expected",
    [
        # Postgres → sqlite (the main migration path for the e2e tests).
        ("BIGINT", "sqlite", "INTEGER"),
        ("TIMESTAMPTZ", "sqlite", "TEXT"),
        ("JSONB", "sqlite", "TEXT"),
        ("VARCHAR(255)", "sqlite", "TEXT"),
        # Postgres → mysql.
        ("TIMESTAMPTZ", "mysql", "DATETIME"),
        ("JSONB", "mysql", "JSON"),
        ("DOUBLE PRECISION", "mysql", "DOUBLE"),
        # Round-trip same dialect = no-op shape.
        ("INTEGER", "postgres", "INTEGER"),
    ],
)
def test_translate_one_call(raw: str, dialect: str, expected: str) -> None:
    assert translate(raw, target_dialect=dialect) == expected


# ---------- Phase AAQ (2026-05-29) — Vertica + MSSQL dialects ----------


@pytest.mark.parametrize(
    "canonical, expected",
    [
        (CanonicalType.INTEGER, "INTEGER"),
        (CanonicalType.BIGINT, "BIGINT"),
        (CanonicalType.REAL, "FLOAT"),
        (CanonicalType.DOUBLE, "DOUBLE PRECISION"),
        (CanonicalType.TEXT, "LONG VARCHAR"),
        (CanonicalType.BOOLEAN, "BOOLEAN"),
        (CanonicalType.TIMESTAMP, "TIMESTAMPTZ"),
        (CanonicalType.JSON, "LONG VARCHAR"),  # no native JSON
        (CanonicalType.BLOB, "VARBINARY"),
    ],
)
def test_render_canonical_vertica(canonical: CanonicalType, expected: str) -> None:
    assert render_canonical(TypeSpec(canonical), dialect="vertica") == expected


@pytest.mark.parametrize(
    "canonical, expected",
    [
        (CanonicalType.INTEGER, "INT"),
        (CanonicalType.BIGINT, "BIGINT"),
        (CanonicalType.DOUBLE, "FLOAT"),
        (CanonicalType.TEXT, "NVARCHAR(MAX)"),
        (CanonicalType.BOOLEAN, "BIT"),
        (CanonicalType.TIMESTAMP, "DATETIME2"),
        (CanonicalType.JSON, "NVARCHAR(MAX)"),  # JSON helpers over NVARCHAR
        (CanonicalType.BLOB, "VARBINARY(MAX)"),
    ],
)
def test_render_canonical_mssql(canonical: CanonicalType, expected: str) -> None:
    assert render_canonical(TypeSpec(canonical), dialect="mssql") == expected


def test_render_varchar_keeps_length_vertica_mssql() -> None:
    """VARCHAR length round-trips through both new dialects so a
    ``VARCHAR(64)`` from postgres lands as the same width in vertica
    and ``NVARCHAR(64)`` in MSSQL."""
    spec = TypeSpec(CanonicalType.VARCHAR, length=64)
    assert render_canonical(spec, dialect="vertica") == "VARCHAR(64)"
    assert render_canonical(spec, dialect="mssql") == "NVARCHAR(64)"


def test_render_decimal_keeps_precision_scale_vertica_mssql() -> None:
    spec = TypeSpec(CanonicalType.DECIMAL, precision=10, scale=2)
    assert render_canonical(spec, dialect="vertica") == "NUMERIC(10,2)"
    assert render_canonical(spec, dialect="mssql") == "DECIMAL(10,2)"


@pytest.mark.parametrize(
    "raw, expected",
    [
        # MSSQL-specific vendor names should normalise correctly so a
        # source that reports ``NVARCHAR(255)`` lands as the right
        # canonical with length preserved.
        ("NVARCHAR(255)", CanonicalType.VARCHAR),
        ("NCHAR(10)", CanonicalType.VARCHAR),
        ("BIT", CanonicalType.BOOLEAN),
        ("DATETIME2", CanonicalType.TIMESTAMP),
        ("DATETIMEOFFSET", CanonicalType.TIMESTAMP),
        ("SMALLDATETIME", CanonicalType.TIMESTAMP),
        ("MONEY", CanonicalType.DECIMAL),
        ("UNIQUEIDENTIFIER", CanonicalType.TEXT),
        # Vertica's LONG VARCHAR should fold to TEXT (length-unbounded).
        ("LONG VARCHAR", CanonicalType.TEXT),
        ("NTEXT", CanonicalType.TEXT),
    ],
)
def test_normalize_mssql_and_vertica_vendor_types(raw: str, expected: CanonicalType) -> None:
    assert normalize_db_type(raw).canonical is expected


def test_nvarchar_length_round_trips() -> None:
    """A MSSQL source reporting NVARCHAR(255) must keep its length on
    the canonical, so the destination dialect can render it back."""
    spec = normalize_db_type("NVARCHAR(255)")
    assert spec.canonical is CanonicalType.VARCHAR
    assert spec.length == 255
    assert render_canonical(spec, dialect="postgres") == "VARCHAR(255)"
    assert render_canonical(spec, dialect="mssql") == "NVARCHAR(255)"
    assert render_canonical(spec, dialect="vertica") == "VARCHAR(255)"


@pytest.mark.parametrize(
    "raw, dialect, expected",
    [
        # postgres → vertica typical migration: BIGINT survives, JSONB
        # becomes LONG VARCHAR, TIMESTAMPTZ stays.
        ("BIGINT", "vertica", "BIGINT"),
        ("JSONB", "vertica", "LONG VARCHAR"),
        ("TIMESTAMPTZ", "vertica", "TIMESTAMPTZ"),
        # MSSQL ↔ postgres: BIT becomes BOOLEAN, DATETIME2 becomes TIMESTAMPTZ.
        ("BIT", "postgres", "BOOLEAN"),
        ("DATETIME2", "postgres", "TIMESTAMPTZ"),
        # postgres → mssql: JSONB → NVARCHAR(MAX), BIGINT survives.
        ("JSONB", "mssql", "NVARCHAR(MAX)"),
        ("BIGINT", "mssql", "BIGINT"),
        ("BOOLEAN", "mssql", "BIT"),
    ],
)
def test_translate_cross_db_vertica_mssql(raw: str, dialect: str, expected: str) -> None:
    assert translate(raw, target_dialect=dialect) == expected
