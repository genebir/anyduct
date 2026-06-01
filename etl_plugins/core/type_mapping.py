"""DB-agnostic type mapping for cross-DB replication (Phase VV, 2026-05-29).

When a pipeline replicates ``postgres.orders → mysql.orders`` (or any other
source/sink combination), the column types reported by the *source* are
expressed in that DB's vocabulary (``BIGINT``, ``TIMESTAMPTZ``, ``JSONB``,
``VARCHAR(255)``, …). The sink needs the *same logical types* rendered in
its own dialect — ``BIGINT`` for postgres ↔ ``BIGINT`` for mysql ↔
``INTEGER`` for sqlite, ``TIMESTAMPTZ`` ↔ ``DATETIME`` ↔ ``TEXT``, etc.

This module provides:

* :class:`CanonicalType` — the small, opinionated set of types every
  RDBMS connector targets. We deliberately keep this short so adding a
  new dialect (snowflake / duckdb / clickhouse) means writing one
  ``DIALECT_DDL_TABLE`` entry, not enumerating 30 variants.
* :func:`normalize_db_type` — parse a vendor type string (``BIGINT``,
  ``VARCHAR(255)``, ``timestamp with time zone``) into a
  :class:`CanonicalType`.
* :func:`render_canonical` — render a :class:`CanonicalType` back into a
  vendor-specific DDL fragment.

Edge cases handled in one place rather than per connector:

* Length specifiers (``VARCHAR(255)``) — kept on the canonical entry as
  an attribute, but most dialects drop them for ``TEXT`` -shaped columns.
* SQLite type affinity — everything collapses to ``INTEGER``/``REAL``/
  ``TEXT``/``BLOB``/``NUMERIC``; the rendering helper does the collapse.
* Case + whitespace — input strings are normalised before lookup.

Why "canonical" and not "OpenLineage type" or "Arrow"? The mapping table
lives where the connectors live (this package) so a connector can
participate without pulling in a new dependency. If we later want to
emit OpenLineage facets, this is the place that knows the canonical
type and can translate to that format too.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CanonicalType(StrEnum):
    """Connector-vendor-neutral column types.

    The set is intentionally small. New canonical types should be added
    only when a real connector can't express what it needs through the
    existing options (e.g. ``DECIMAL`` was added so financial source data
    isn't silently truncated to ``DOUBLE``)."""

    INTEGER = "integer"
    BIGINT = "bigint"
    SMALLINT = "smallint"
    REAL = "real"  # 32-bit float
    DOUBLE = "double"  # 64-bit float
    DECIMAL = "decimal"
    TEXT = "text"  # variable-length string, no length cap
    VARCHAR = "varchar"  # length-capped string (carries optional length)
    BOOLEAN = "boolean"
    TIMESTAMP = "timestamp"  # tz-aware where supported
    DATE = "date"
    JSON = "json"
    BLOB = "blob"  # binary


@dataclass(frozen=True)
class TypeSpec:
    """A normalised column type + optional length / precision / scale.

    ``length`` applies to ``VARCHAR``; ``precision`` / ``scale`` apply to
    ``DECIMAL``. Other types ignore them.
    """

    canonical: CanonicalType
    length: int | None = None
    precision: int | None = None
    scale: int | None = None


# ---- Normalisation -------------------------------------------------------


# Source vendor → CanonicalType. Lower-cased + stripped before lookup.
_VENDOR_TO_CANONICAL: dict[str, CanonicalType] = {
    # ---- INTEGER family -----
    "int": CanonicalType.INTEGER,
    "integer": CanonicalType.INTEGER,
    "int4": CanonicalType.INTEGER,
    "mediumint": CanonicalType.INTEGER,
    "smallint": CanonicalType.SMALLINT,
    "int2": CanonicalType.SMALLINT,
    "tinyint": CanonicalType.SMALLINT,
    "bigint": CanonicalType.BIGINT,
    "int8": CanonicalType.BIGINT,
    # ---- FLOAT family -----
    "real": CanonicalType.REAL,
    "float": CanonicalType.REAL,
    "float4": CanonicalType.REAL,
    "double": CanonicalType.DOUBLE,
    "double precision": CanonicalType.DOUBLE,
    "float8": CanonicalType.DOUBLE,
    # ---- DECIMAL -----
    "decimal": CanonicalType.DECIMAL,
    "numeric": CanonicalType.DECIMAL,
    # MSSQL currency types — DECIMAL preserves precision better than
    # silently demoting to DOUBLE.
    "money": CanonicalType.DECIMAL,
    "smallmoney": CanonicalType.DECIMAL,
    # ---- STRINGS -----
    "varchar": CanonicalType.VARCHAR,
    "character varying": CanonicalType.VARCHAR,
    "char": CanonicalType.VARCHAR,
    # Phase AAQ — MSSQL unicode variants. Length round-trips via the
    # VARCHAR canonical (NVARCHAR(255) → VARCHAR canonical → renders
    # back as NVARCHAR(255) in MSSQL or VARCHAR(255) elsewhere).
    "nvarchar": CanonicalType.VARCHAR,
    "nchar": CanonicalType.VARCHAR,
    "ntext": CanonicalType.TEXT,
    # MSSQL's GUIDs are 36-char strings on the wire — TEXT preserves
    # them across DBs without forcing every dialect to ship a UUID type.
    "uniqueidentifier": CanonicalType.TEXT,
    # Phase AAQ — Vertica's large-text type. Length-unbounded so we
    # collapse to TEXT rather than VARCHAR.
    "long varchar": CanonicalType.TEXT,
    "text": CanonicalType.TEXT,
    "longtext": CanonicalType.TEXT,
    "mediumtext": CanonicalType.TEXT,
    "tinytext": CanonicalType.TEXT,
    # ---- BOOLEAN -----
    "boolean": CanonicalType.BOOLEAN,
    "bool": CanonicalType.BOOLEAN,
    # MSSQL's boolean column type.
    "bit": CanonicalType.BOOLEAN,
    # ---- TIME -----
    "timestamp": CanonicalType.TIMESTAMP,
    "timestamp with time zone": CanonicalType.TIMESTAMP,
    "timestamp without time zone": CanonicalType.TIMESTAMP,
    "timestamptz": CanonicalType.TIMESTAMP,
    "datetime": CanonicalType.TIMESTAMP,
    # MSSQL temporal types.
    "datetime2": CanonicalType.TIMESTAMP,
    "datetimeoffset": CanonicalType.TIMESTAMP,
    "smalldatetime": CanonicalType.TIMESTAMP,
    "date": CanonicalType.DATE,
    # ---- JSON -----
    "json": CanonicalType.JSON,
    "jsonb": CanonicalType.JSON,
    # ---- BINARY -----
    "blob": CanonicalType.BLOB,
    "bytea": CanonicalType.BLOB,
    "binary": CanonicalType.BLOB,
    "varbinary": CanonicalType.BLOB,
}


def normalize_db_type(raw: str) -> TypeSpec:
    """Parse a vendor type string into a :class:`TypeSpec`.

    ``VARCHAR(255)`` keeps the 255 on the spec; ``DECIMAL(10,2)`` keeps
    both precision and scale. Unknown vendor types fall back to
    :class:`CanonicalType.TEXT` with a length-less spec — that's the
    safest default (any value round-trips through a text column).
    """
    if not raw:
        return TypeSpec(CanonicalType.TEXT)
    s = raw.strip().lower()
    # Pull out the parenthesised arguments if any.
    args: tuple[int, ...] = ()
    if "(" in s and s.endswith(")"):
        base, _, arg_str = s.partition("(")
        s = base.strip()
        arg_str = arg_str.rstrip(")")
        try:
            args = tuple(int(p.strip()) for p in arg_str.split(",") if p.strip())
        except ValueError:
            args = ()
    canonical = _VENDOR_TO_CANONICAL.get(s, CanonicalType.TEXT)
    if canonical is CanonicalType.VARCHAR and args:
        return TypeSpec(canonical, length=args[0])
    if canonical is CanonicalType.DECIMAL and len(args) >= 1:
        return TypeSpec(
            canonical,
            precision=args[0],
            scale=args[1] if len(args) > 1 else 0,
        )
    return TypeSpec(canonical)


# ---- Rendering -----------------------------------------------------------


# Per-dialect DDL fragment table. Lookups against this table are total —
# every CanonicalType must have a render rule per dialect; otherwise the
# build-time tests below would surface the gap immediately.
_DIALECT_DDL: dict[str, dict[CanonicalType, str]] = {
    "sqlite": {
        CanonicalType.INTEGER: "INTEGER",
        # SQLite type affinity collapses bigint/smallint to INTEGER.
        CanonicalType.BIGINT: "INTEGER",
        CanonicalType.SMALLINT: "INTEGER",
        CanonicalType.REAL: "REAL",
        CanonicalType.DOUBLE: "REAL",
        CanonicalType.DECIMAL: "NUMERIC",
        CanonicalType.TEXT: "TEXT",
        CanonicalType.VARCHAR: "TEXT",
        CanonicalType.BOOLEAN: "INTEGER",
        CanonicalType.TIMESTAMP: "TEXT",  # ISO 8601 strings round-trip best
        CanonicalType.DATE: "TEXT",
        CanonicalType.JSON: "TEXT",
        CanonicalType.BLOB: "BLOB",
    },
    "postgres": {
        CanonicalType.INTEGER: "INTEGER",
        CanonicalType.BIGINT: "BIGINT",
        CanonicalType.SMALLINT: "SMALLINT",
        CanonicalType.REAL: "REAL",
        CanonicalType.DOUBLE: "DOUBLE PRECISION",
        CanonicalType.DECIMAL: "NUMERIC",
        CanonicalType.TEXT: "TEXT",
        CanonicalType.VARCHAR: "VARCHAR",
        CanonicalType.BOOLEAN: "BOOLEAN",
        CanonicalType.TIMESTAMP: "TIMESTAMPTZ",
        CanonicalType.DATE: "DATE",
        CanonicalType.JSON: "JSONB",
        CanonicalType.BLOB: "BYTEA",
    },
    "mysql": {
        CanonicalType.INTEGER: "INT",
        CanonicalType.BIGINT: "BIGINT",
        CanonicalType.SMALLINT: "SMALLINT",
        CanonicalType.REAL: "FLOAT",
        CanonicalType.DOUBLE: "DOUBLE",
        CanonicalType.DECIMAL: "DECIMAL",
        CanonicalType.TEXT: "TEXT",
        CanonicalType.VARCHAR: "VARCHAR",
        CanonicalType.BOOLEAN: "TINYINT(1)",
        CanonicalType.TIMESTAMP: "DATETIME",
        CanonicalType.DATE: "DATE",
        CanonicalType.JSON: "JSON",
        CanonicalType.BLOB: "BLOB",
    },
    # Vertica (Phase AAQ, 2026-05-29) — column-oriented analytical DB.
    # Type vocabulary is close to postgres with a few twists:
    #   * TIMESTAMPTZ exists but the canonical name is TIMESTAMP WITH TZ;
    #     "TIMESTAMPTZ" is also accepted, we pick the shorter form.
    #   * No native JSON column; the standard pattern is to store JSON
    #     payloads in VARCHAR/LONG VARCHAR and parse downstream.
    #   * BOOLEAN is first-class.
    #   * VARBYTES is the BLOB equivalent.
    "vertica": {
        CanonicalType.INTEGER: "INTEGER",
        CanonicalType.BIGINT: "BIGINT",
        CanonicalType.SMALLINT: "SMALLINT",
        CanonicalType.REAL: "FLOAT",
        CanonicalType.DOUBLE: "DOUBLE PRECISION",
        CanonicalType.DECIMAL: "NUMERIC",
        # LONG VARCHAR holds up to 32MB — closest to TEXT in semantics.
        CanonicalType.TEXT: "LONG VARCHAR",
        CanonicalType.VARCHAR: "VARCHAR",
        CanonicalType.BOOLEAN: "BOOLEAN",
        CanonicalType.TIMESTAMP: "TIMESTAMPTZ",
        CanonicalType.DATE: "DATE",
        # No native JSON — JSON payloads live in LONG VARCHAR.
        CanonicalType.JSON: "LONG VARCHAR",
        CanonicalType.BLOB: "VARBINARY",
    },
    # MSSQL (Phase AAQ, 2026-05-29) — SQL Server / Azure SQL.
    # Type vocabulary picks the safe, modern subset:
    #   * NVARCHAR(MAX) for TEXT to dodge SQL Server's deprecated TEXT type.
    #   * BIT for BOOLEAN.
    #   * DATETIME2 for TIMESTAMP (DATETIME is legacy + lower precision).
    #   * NVARCHAR(MAX) for JSON (SQL Server has JSON helpers over plain
    #     NVARCHAR, no dedicated column type).
    #   * VARBINARY(MAX) for BLOB.
    "mssql": {
        CanonicalType.INTEGER: "INT",
        CanonicalType.BIGINT: "BIGINT",
        CanonicalType.SMALLINT: "SMALLINT",
        CanonicalType.REAL: "REAL",
        CanonicalType.DOUBLE: "FLOAT",
        CanonicalType.DECIMAL: "DECIMAL",
        # Use NVARCHAR(MAX) — SQL Server's modern unicode "large text"
        # column. The plain ``TEXT`` type is deprecated.
        CanonicalType.TEXT: "NVARCHAR(MAX)",
        CanonicalType.VARCHAR: "NVARCHAR",
        CanonicalType.BOOLEAN: "BIT",
        CanonicalType.TIMESTAMP: "DATETIME2",
        CanonicalType.DATE: "DATE",
        CanonicalType.JSON: "NVARCHAR(MAX)",
        CanonicalType.BLOB: "VARBINARY(MAX)",
    },
}


def render_canonical(spec: TypeSpec, dialect: str) -> str:
    """Render a :class:`TypeSpec` to the target dialect's DDL fragment.

    Length / precision / scale are appended for the few types where they
    matter (``VARCHAR(255)`` / ``DECIMAL(10,2)``) and dropped for the
    rest. Unknown dialects fall back to sqlite's table — sqlite's
    affinity rules are permissive enough to accept anything, so a typo
    in a connector's dialect tag downgrades rather than crashes.

    Note on length suffixes: a length spec is only emitted when the
    target *base* type accepts it. SQLite collapses VARCHAR to TEXT,
    which doesn't carry a length — so a ``VARCHAR(255)`` source column
    renders as plain ``TEXT`` for sqlite, not ``TEXT(255)``.
    """
    table = _DIALECT_DDL.get(dialect, _DIALECT_DDL["sqlite"])
    base = table[spec.canonical]
    base_upper = base.upper()
    if spec.canonical is CanonicalType.VARCHAR and spec.length and "VARCHAR" in base_upper:
        return f"{base}({spec.length})"
    if (
        spec.canonical is CanonicalType.DECIMAL
        and spec.precision
        and any(name in base_upper for name in ("NUMERIC", "DECIMAL"))
    ):
        if spec.scale is not None:
            return f"{base}({spec.precision},{spec.scale})"
        return f"{base}({spec.precision})"
    return base


def translate(raw_type: str, *, target_dialect: str) -> str:
    """Convenience: normalise *and* render in one call.

    The two-step form is preferred when emitting many columns to the
    same dialect (the rendering helper is cheaper if you've already
    parsed). This wrapper is for one-off ``"BIGINT" → "INTEGER"`` style
    calls (e.g. in a logging message or a builder lint hint)."""
    return render_canonical(normalize_db_type(raw_type), dialect=target_dialect)


__all__ = [
    "CanonicalType",
    "TypeSpec",
    "normalize_db_type",
    "render_canonical",
    "translate",
]
