/**
 * Cross-DB type mapping preview — Phase ABF (2026-06-01).
 *
 * Mirrors the *core* mapping in
 * ``etl_plugins/core/type_mapping.py`` so the migration form can
 * show operators what their source columns will look like on the
 * destination *without* a server roundtrip. The runtime is still
 * the authority — this preview is best-effort.
 *
 * Why duplicate the Python table? It changes rarely; copying the
 * minimal vocabulary keeps the form snappy and avoids a new REST
 * endpoint just to render a hint. New canonical types should be
 * added in both places.
 */

export type CanonicalType =
  | "INTEGER"
  | "BIGINT"
  | "SMALLINT"
  | "REAL"
  | "DOUBLE"
  | "DECIMAL"
  | "TEXT"
  | "VARCHAR"
  | "BOOLEAN"
  | "TIMESTAMP"
  | "DATE"
  | "JSON"
  | "BLOB";

interface ParsedType {
  canonical: CanonicalType;
  length?: number;
  precision?: number;
  scale?: number;
}

const VENDOR_TO_CANONICAL: Record<string, CanonicalType> = {
  // integers
  int: "INTEGER",
  integer: "INTEGER",
  int4: "INTEGER",
  mediumint: "INTEGER",
  smallint: "SMALLINT",
  int2: "SMALLINT",
  tinyint: "SMALLINT",
  bigint: "BIGINT",
  int8: "BIGINT",
  // floats
  real: "REAL",
  float: "REAL",
  float4: "REAL",
  double: "DOUBLE",
  "double precision": "DOUBLE",
  float8: "DOUBLE",
  // decimal
  decimal: "DECIMAL",
  numeric: "DECIMAL",
  money: "DECIMAL",
  smallmoney: "DECIMAL",
  // strings
  varchar: "VARCHAR",
  "character varying": "VARCHAR",
  char: "VARCHAR",
  nvarchar: "VARCHAR",
  nchar: "VARCHAR",
  ntext: "TEXT",
  uniqueidentifier: "TEXT",
  "long varchar": "TEXT",
  text: "TEXT",
  longtext: "TEXT",
  mediumtext: "TEXT",
  tinytext: "TEXT",
  // boolean
  boolean: "BOOLEAN",
  bool: "BOOLEAN",
  bit: "BOOLEAN",
  // time
  timestamp: "TIMESTAMP",
  "timestamp with time zone": "TIMESTAMP",
  "timestamp without time zone": "TIMESTAMP",
  timestamptz: "TIMESTAMP",
  datetime: "TIMESTAMP",
  datetime2: "TIMESTAMP",
  datetimeoffset: "TIMESTAMP",
  smalldatetime: "TIMESTAMP",
  date: "DATE",
  // json
  json: "JSON",
  jsonb: "JSON",
  // binary
  blob: "BLOB",
  bytea: "BLOB",
  binary: "BLOB",
  varbinary: "BLOB",
};

const DIALECT_DDL: Record<string, Partial<Record<CanonicalType, string>>> = {
  sqlite: {
    INTEGER: "INTEGER",
    BIGINT: "INTEGER",
    SMALLINT: "INTEGER",
    REAL: "REAL",
    DOUBLE: "REAL",
    DECIMAL: "NUMERIC",
    TEXT: "TEXT",
    VARCHAR: "TEXT",
    BOOLEAN: "INTEGER",
    TIMESTAMP: "TEXT",
    DATE: "TEXT",
    JSON: "TEXT",
    BLOB: "BLOB",
  },
  postgres: {
    INTEGER: "INTEGER",
    BIGINT: "BIGINT",
    SMALLINT: "SMALLINT",
    REAL: "REAL",
    DOUBLE: "DOUBLE PRECISION",
    DECIMAL: "NUMERIC",
    TEXT: "TEXT",
    VARCHAR: "VARCHAR",
    BOOLEAN: "BOOLEAN",
    TIMESTAMP: "TIMESTAMPTZ",
    DATE: "DATE",
    JSON: "JSONB",
    BLOB: "BYTEA",
  },
  mysql: {
    INTEGER: "INT",
    BIGINT: "BIGINT",
    SMALLINT: "SMALLINT",
    REAL: "FLOAT",
    DOUBLE: "DOUBLE",
    DECIMAL: "DECIMAL",
    TEXT: "TEXT",
    VARCHAR: "VARCHAR",
    BOOLEAN: "TINYINT(1)",
    TIMESTAMP: "DATETIME",
    DATE: "DATE",
    JSON: "JSON",
    BLOB: "BLOB",
  },
  vertica: {
    INTEGER: "INTEGER",
    BIGINT: "BIGINT",
    SMALLINT: "SMALLINT",
    REAL: "FLOAT",
    DOUBLE: "DOUBLE PRECISION",
    DECIMAL: "NUMERIC",
    TEXT: "LONG VARCHAR",
    VARCHAR: "VARCHAR",
    BOOLEAN: "BOOLEAN",
    TIMESTAMP: "TIMESTAMPTZ",
    DATE: "DATE",
    JSON: "LONG VARCHAR",
    BLOB: "VARBINARY",
  },
  mssql: {
    INTEGER: "INT",
    BIGINT: "BIGINT",
    SMALLINT: "SMALLINT",
    REAL: "REAL",
    DOUBLE: "FLOAT",
    DECIMAL: "DECIMAL",
    TEXT: "NVARCHAR(MAX)",
    VARCHAR: "NVARCHAR",
    BOOLEAN: "BIT",
    TIMESTAMP: "DATETIME2",
    DATE: "DATE",
    JSON: "NVARCHAR(MAX)",
    BLOB: "VARBINARY(MAX)",
  },
};

/** Parse a vendor type string into the canonical spec. Unknown
 *  vendor types fall back to ``TEXT`` so the preview is always
 *  something (the runtime makes the same choice). */
export function normalizeDbType(raw: string): ParsedType {
  if (!raw) return { canonical: "TEXT" };
  const trimmed = raw.trim().toLowerCase();
  let base = trimmed;
  let args: number[] = [];
  const parenStart = trimmed.indexOf("(");
  if (parenStart > 0 && trimmed.endsWith(")")) {
    base = trimmed.slice(0, parenStart).trim();
    const argStr = trimmed.slice(parenStart + 1, -1);
    args = argStr
      .split(",")
      .map((s) => parseInt(s.trim(), 10))
      .filter((n) => !Number.isNaN(n));
  }
  const canonical = VENDOR_TO_CANONICAL[base] ?? "TEXT";
  if (canonical === "VARCHAR" && args.length > 0) {
    return { canonical, length: args[0] };
  }
  if (canonical === "DECIMAL" && args.length > 0) {
    return {
      canonical,
      precision: args[0],
      scale: args.length > 1 ? args[1] : 0,
    };
  }
  return { canonical };
}

/** Render a canonical spec back to a target dialect's DDL fragment.
 *  Unknown dialects fall back to sqlite (matches the Python
 *  fallback). */
export function renderCanonical(
  spec: ParsedType,
  dialect: string,
): string {
  const table = DIALECT_DDL[dialect] ?? DIALECT_DDL.sqlite;
  const base = table[spec.canonical] ?? "TEXT";
  const baseUpper = base.toUpperCase();
  if (
    spec.canonical === "VARCHAR" &&
    spec.length !== undefined &&
    baseUpper.includes("VARCHAR")
  ) {
    return `${base}(${spec.length})`;
  }
  if (
    spec.canonical === "DECIMAL" &&
    spec.precision !== undefined &&
    (baseUpper.includes("NUMERIC") || baseUpper.includes("DECIMAL"))
  ) {
    if (spec.scale !== undefined && spec.scale > 0) {
      return `${base}(${spec.precision},${spec.scale})`;
    }
    return `${base}(${spec.precision})`;
  }
  return base;
}

/** One-call translate from a source vendor type string into the
 *  target dialect's DDL fragment. */
export function translateType(sourceType: string, targetDialect: string): string {
  return renderCanonical(normalizeDbType(sourceType), targetDialect);
}
