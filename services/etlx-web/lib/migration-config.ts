/**
 * Migration form ↔ PipelineConfig serialisation — Phase AAN3 (2026-05-29).
 *
 * Re-shaped from "two halves that look like ETL" into something
 * unmistakably migration-shaped:
 *
 * * **table picker, not query** — the user picks a source table; the
 *   runtime gets ``SELECT * FROM <table>``.
 * * **strategy radio, not (mode + if_exists + key_columns)** — three
 *   humanised choices that map onto safe defaults:
 *     - ``snapshot``  → ``mode='overwrite'`` + ``auto_create_if_exists='drop'``
 *       (daily snapshot rebuild, schema-drift tolerant, ADR-0071)
 *     - ``append``    → ``mode='append'`` + ``cursor_column``
 *       (only new rows since last run; needs cursor)
 *     - ``mirror``    → ``mode='upsert'`` + ``key_columns`` (+ PK auto-emit, ADR-0072)
 * * **always auto_create_table=true** — the migration surface
 *   doesn't surface that as a toggle; it's the whole point.
 *
 * The output is still a normal linear :class:`PipelineConfig` so
 * dry-run / lint / worker / catalog all just work.
 */

export type MigrationStrategy = "snapshot" | "append" | "mirror";

/** RDBMS connector types that implement ``SchemaWriter``
 *  (``ensure_table``) — Phase VV / ADR-0066, Phase AAQ adds Vertica
 *  + MSSQL. ``auto_create_table`` only does anything on these, so
 *  the migration form filters connections to this set. */
export const MIGRATION_SUPPORTED_TYPES = new Set([
  "postgres",
  "mysql",
  "sqlite",
  "vertica",
  "mssql",
]);

export interface MigrationFormData {
  /** Connection NAME (matches what the runtime resolves). */
  sourceConnection: string;
  /** Fully-qualified source table — e.g. ``public.orders`` for
   *  Postgres, bare ``orders`` for SQLite. The runtime issues
   *  ``SELECT * FROM <table>`` against it. */
  sourceTable: string;
  sinkConnection: string;
  sinkTable: string;
  strategy: MigrationStrategy;
  /** Required for ``mirror`` — comma-separated. Become PRIMARY KEY
   *  on the auto-created sink (ADR-0072). */
  keyColumns: string;
  /** Required for ``append`` — single column whose value the
   *  runtime tracks (cursor state, Step 6.1). */
  cursorColumn: string;
}

export const DEFAULT_MIGRATION_FORM: MigrationFormData = {
  sourceConnection: "",
  sourceTable: "",
  sinkConnection: "",
  sinkTable: "",
  strategy: "snapshot",
  keyColumns: "",
  cursorColumn: "",
};

export interface MigrationFormErrors {
  sourceConnection?: string;
  sourceTable?: string;
  sinkConnection?: string;
  sinkTable?: string;
  keyColumns?: string;
  cursorColumn?: string;
}

export function validateMigrationForm(
  form: MigrationFormData,
): MigrationFormErrors {
  const errs: MigrationFormErrors = {};
  if (!form.sourceConnection) errs.sourceConnection = "required";
  if (!form.sourceTable.trim()) errs.sourceTable = "required";
  if (!form.sinkConnection) errs.sinkConnection = "required";
  if (!form.sinkTable.trim()) errs.sinkTable = "required";
  if (form.strategy === "mirror") {
    if (splitKeyColumns(form.keyColumns).length === 0) {
      errs.keyColumns = "required";
    }
  }
  if (form.strategy === "append" && !form.cursorColumn.trim()) {
    errs.cursorColumn = "required";
  }
  return errs;
}

export function splitKeyColumns(text: string): string[] {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

/** Map a strategy onto its underlying sink config keys. Pure +
 *  exported so tests can pin the mapping. */
export function strategyToSinkShape(
  strategy: MigrationStrategy,
  keyColumns: string,
): {
  mode: "append" | "overwrite" | "upsert";
  auto_create_if_exists: "skip" | "drop" | "error";
  key_columns?: string[];
} {
  if (strategy === "snapshot") {
    return { mode: "overwrite", auto_create_if_exists: "drop" };
  }
  if (strategy === "mirror") {
    return {
      mode: "upsert",
      auto_create_if_exists: "skip",
      key_columns: splitKeyColumns(keyColumns),
    };
  }
  // append — purely additive; ``auto_create_if_exists`` stays skip
  // so the second run doesn't blow away yesterday's rows.
  return { mode: "append", auto_create_if_exists: "skip" };
}

/** Build the ``PipelineConfig`` JSON the server stores. */
export function buildMigrationConfig(
  name: string,
  form: MigrationFormData,
): Record<string, unknown> {
  const sinkShape = strategyToSinkShape(form.strategy, form.keyColumns);
  const sink: Record<string, unknown> = {
    connection: form.sinkConnection,
    table: form.sinkTable.trim(),
    mode: sinkShape.mode,
    auto_create_table: true,
  };
  // Only emit if_exists when it diverges from the default (skip) —
  // keeps the saved JSON minimal and BC-compatible.
  if (sinkShape.auto_create_if_exists !== "skip") {
    sink.auto_create_if_exists = sinkShape.auto_create_if_exists;
  }
  if (sinkShape.key_columns) {
    sink.key_columns = sinkShape.key_columns;
  }
  const source: Record<string, unknown> = {
    connection: form.sourceConnection,
    // Migration mental model: "copy this table". The runtime takes
    // care of the SELECT.
    query: `SELECT * FROM ${form.sourceTable.trim()}`,
  };
  if (form.strategy === "append" && form.cursorColumn.trim()) {
    source.cursor_column = form.cursorColumn.trim();
  }
  return {
    name,
    mode: "batch",
    source,
    sink,
  };
}

/** Inverse of ``buildMigrationConfig``. Returns ``null`` if the
 *  config doesn't look like a migration shape (graph mode, fan-out,
 *  auto_create_table off, or a hand-written query the table picker
 *  can't represent). */
export function parseMigrationConfig(
  config: Record<string, unknown> | null | undefined,
): MigrationFormData | null {
  if (!config || typeof config !== "object") return null;
  if (config.graph) return null;
  if (Array.isArray(config.sinks) && config.sinks.length > 0) return null;

  const src = config.source as Record<string, unknown> | undefined;
  const snk = config.sink as Record<string, unknown> | undefined;
  if (!src || !snk) return null;
  if (snk.auto_create_table !== true) return null;

  // The migration form emits ``SELECT * FROM <table>`` only. Anything
  // else — JOINs, WHEREs, computed columns — is too rich for the
  // table-picker model; bail out so the user opens it in the builder.
  const table = parseSelectStarTable(
    typeof src.query === "string" ? src.query : "",
  );
  if (!table) return null;

  const mode = typeof snk.mode === "string" ? snk.mode : "append";
  const ifExists =
    snk.auto_create_if_exists === "drop" ||
    snk.auto_create_if_exists === "error"
      ? (snk.auto_create_if_exists as "drop" | "error")
      : "skip";

  let strategy: MigrationStrategy;
  if (mode === "overwrite" && ifExists === "drop") {
    strategy = "snapshot";
  } else if (mode === "upsert") {
    strategy = "mirror";
  } else if (mode === "append") {
    strategy = "append";
  } else {
    // mode=overwrite + if_exists=skip / error is a corner case we
    // don't surface in the strategy picker. Bail to builder so
    // round-trip can't silently rewrite it.
    return null;
  }

  const keyColumns = Array.isArray(snk.key_columns)
    ? snk.key_columns
        .filter((c): c is string => typeof c === "string")
        .join(", ")
    : "";
  const cursorColumn =
    typeof src.cursor_column === "string" ? src.cursor_column : "";

  return {
    sourceConnection: typeof src.connection === "string" ? src.connection : "",
    sourceTable: table,
    sinkConnection: typeof snk.connection === "string" ? snk.connection : "",
    sinkTable: typeof snk.table === "string" ? snk.table : "",
    strategy,
    keyColumns,
    cursorColumn,
  };
}

/** Extract the table name from a strict ``SELECT * FROM <name>``
 *  query. Returns null for anything richer. */
export function parseSelectStarTable(query: string): string | null {
  const m = /^\s*SELECT\s+\*\s+FROM\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;?\s*$/i.exec(
    query,
  );
  return m ? m[1] : null;
}
