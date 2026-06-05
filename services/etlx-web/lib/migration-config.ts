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
  "snowflake",
  "bigquery",
  "redshift",
  "clickhouse",
  "cassandra",
]);

/** Phase AAS (2026-06-01) — what *unit* the migration replicates.
 *
 * * ``single`` — one source table → one destination table (the
 *   original Migration form behaviour).
 * * ``schema`` — every selected table inside a source schema → the
 *   matching destination schema. Saved as **one pipeline per
 *   table** so the existing list / Last run / Run now flow keeps
 *   working unchanged; the form just batches the create calls.
 */
export type MigrationMode = "single" | "schema";

export interface MigrationFormData {
  /** Whether the user is migrating one table or an entire schema. */
  mode: MigrationMode;
  /** Connection NAME (matches what the runtime resolves). */
  sourceConnection: string;
  /** Single-mode: fully-qualified source table — e.g.
   *  ``public.orders`` for Postgres, bare ``orders`` for SQLite. */
  sourceTable: string;
  /** Schema-mode: the schema whose tables are replicated. */
  sourceSchema: string;
  /** Schema-mode: tables the user picked (qualified
   *  ``schema.table`` strings as returned by
   *  ``connectionsApi.tables``). */
  selectedTables: string[];
  sinkConnection: string;
  sinkTable: string;
  /** Schema-mode: destination schema name. Defaults to the source
   *  schema so the round-trip is identity by default. */
  sinkSchema: string;
  strategy: MigrationStrategy;
  /** Required for ``mirror`` — comma-separated. Become PRIMARY KEY
   *  on the auto-created sink (ADR-0072).
   *  Schema-mode: the same keys are applied to every table, so the
   *  feature is only useful when every selected table shares the
   *  same key column(s). For mixed shapes use single-mode. */
  keyColumns: string;
  /** Required for ``append`` — single column whose value the
   *  runtime tracks (cursor state, Step 6.1). */
  cursorColumn: string;
}

export const DEFAULT_MIGRATION_FORM: MigrationFormData = {
  mode: "single",
  sourceConnection: "",
  sourceTable: "",
  sourceSchema: "",
  selectedTables: [],
  sinkConnection: "",
  sinkTable: "",
  sinkSchema: "",
  strategy: "snapshot",
  keyColumns: "",
  cursorColumn: "",
};

export interface MigrationFormErrors {
  sourceConnection?: string;
  sourceTable?: string;
  sourceSchema?: string;
  selectedTables?: string;
  sinkConnection?: string;
  sinkTable?: string;
  sinkSchema?: string;
  keyColumns?: string;
  cursorColumn?: string;
}

export function validateMigrationForm(
  form: MigrationFormData,
): MigrationFormErrors {
  const errs: MigrationFormErrors = {};
  if (!form.sourceConnection) errs.sourceConnection = "required";
  if (!form.sinkConnection) errs.sinkConnection = "required";

  if (form.mode === "schema") {
    if (!form.sourceSchema.trim()) errs.sourceSchema = "required";
    if (!form.sinkSchema.trim()) errs.sinkSchema = "required";
    if (form.selectedTables.length === 0) errs.selectedTables = "required";
  } else {
    if (!form.sourceTable.trim()) errs.sourceTable = "required";
    if (!form.sinkTable.trim()) errs.sinkTable = "required";
  }
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

  // Loaded migrations are always single-mode on the edit page. The
  // mode toggle is hidden anyway (``nameLocked``) — schema-level
  // creates produce N separate single-table pipelines, each of which
  // round-trips here as a normal single migration.
  return {
    mode: "single",
    sourceConnection: typeof src.connection === "string" ? src.connection : "",
    sourceTable: table,
    sourceSchema: "",
    selectedTables: [],
    sinkConnection: typeof snk.connection === "string" ? snk.connection : "",
    sinkTable: typeof snk.table === "string" ? snk.table : "",
    sinkSchema: "",
    strategy,
    keyColumns,
    cursorColumn,
  };
}

// ---------- Phase ACJ (2026-06-04) smart-default column guesses -----------

export interface SourceColumn {
  name: string;
  type: string;
}

/** Mirror's PRIMARY KEY guess — the conventional ``id`` column,
 *  preferring an exact ``id`` then any case-insensitive match.
 *  Returns ``null`` when there's no obvious key. Pure + exported so
 *  the form can apply it both when columns first load *and* when the
 *  strategy changes after the columns are already in hand (ACG only
 *  fired in the columns-load effect, which missed the common
 *  pick-table-then-choose-strategy ordering). */
export function suggestKeyColumn(columns: SourceColumn[]): string | null {
  const exact = columns.find((c) => c.name === "id");
  if (exact) return exact.name;
  const ci = columns.find((c) => c.name.toLowerCase() === "id");
  return ci ? ci.name : null;
}

/** Append's cursor column guess — prefer ``updated_at`` (most
 *  accurate watermark), then ``created_at`` (fine for insert-only
 *  tables), then any ``*_at`` column. Case-insensitive. Counterpart
 *  to {@link suggestKeyColumn}; see ACH for the original inline
 *  version. */
export function suggestCursorColumn(columns: SourceColumn[]): string | null {
  const ci = (n: string) => (c: SourceColumn) => c.name.toLowerCase() === n;
  const updated = columns.find(ci("updated_at"));
  if (updated) return updated.name;
  const created = columns.find(ci("created_at"));
  if (created) return created.name;
  const anyAt = columns.find((c) => c.name.toLowerCase().endsWith("_at"));
  return anyAt ? anyAt.name : null;
}

/** Extract the table name from a strict ``SELECT * FROM <name>``
 *  query. Returns null for anything richer. */
export function parseSelectStarTable(query: string): string | null {
  const m = /^\s*SELECT\s+\*\s+FROM\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;?\s*$/i.exec(
    query,
  );
  return m ? m[1] : null;
}


// ---------- Phase AAS (2026-06-01) schema-level helpers --------------------


export interface SchemaQualifiedTable {
  schema: string | null;
  name: string;
  qualified: string;
}

/** Split ``"schema.table"`` into its parts. Bare ``"orders"`` becomes
 *  ``{schema: null, name: "orders"}``. ``connectionsApi.tables`` returns
 *  schema-qualified strings for postgres / vertica / mssql; sqlite
 *  reports bare names. */
export function parseQualifiedTable(qualified: string): SchemaQualifiedTable {
  const idx = qualified.indexOf(".");
  if (idx <= 0 || idx === qualified.length - 1) {
    return { schema: null, name: qualified, qualified };
  }
  return {
    schema: qualified.slice(0, idx),
    name: qualified.slice(idx + 1),
    qualified,
  };
}

/** Group a list of ``schema.table`` strings by schema. Bare names
 *  land under the ``""`` key. */
export function groupTablesBySchema(
  tables: string[],
): Map<string, SchemaQualifiedTable[]> {
  const m = new Map<string, SchemaQualifiedTable[]>();
  for (const t of tables) {
    const parsed = parseQualifiedTable(t);
    const key = parsed.schema ?? "";
    const bucket = m.get(key);
    if (bucket) bucket.push(parsed);
    else m.set(key, [parsed]);
  }
  return m;
}

export interface BulkMigrationPlanItem {
  /** Pipeline name to create — ``{baseName}_{tableName}``. */
  pipelineName: string;
  /** PipelineConfig JSON to POST. */
  config: Record<string, unknown>;
  /** The source qualified table — surfaced in error toasts. */
  sourceTable: string;
}

/** Schema-mode build: take the form's ``selectedTables`` and emit
 *  one ``buildMigrationConfig``-equivalent entry per table. Each
 *  becomes its own pipeline (own row on the migrations list, own
 *  Run / Last run / history). */
export function buildBulkMigrationConfigs(
  baseName: string,
  form: MigrationFormData,
): BulkMigrationPlanItem[] {
  if (form.mode !== "schema") return [];
  const out: BulkMigrationPlanItem[] = [];
  const sinkSchema = form.sinkSchema.trim() || form.sourceSchema.trim();
  for (const qualified of form.selectedTables) {
    const parsed = parseQualifiedTable(qualified);
    const tableName = parsed.name;
    // Per-table form copy that re-uses ``buildMigrationConfig``'s
    // strategy / mode wiring.
    const perTable: MigrationFormData = {
      ...form,
      mode: "single",
      sourceTable: qualified,
      sinkTable: sinkSchema ? `${sinkSchema}.${tableName}` : tableName,
    };
    const pipelineName = `${baseName.trim()}_${tableName}`;
    out.push({
      pipelineName,
      config: buildMigrationConfig(pipelineName, perTable),
      sourceTable: qualified,
    });
  }
  return out;
}
