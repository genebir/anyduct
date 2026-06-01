/**
 * Migration form ↔ PipelineConfig serialisation — Phase AAN follow-up
 * (2026-05-29).
 *
 * The "Migrations" surface is a dedicated, non-builder form: source
 * + sink + a few switches, no graph canvas. Under the hood the
 * runtime still consumes a normal :class:`PipelineConfig`, so this
 * module is the round-trip between the form's typed shape and the
 * config JSON the server stores.
 *
 * We emit a *linear* config (``source`` + ``sink``) instead of a
 * graph — it keeps the saved JSON minimal and means the existing
 * dry-run / lint / worker paths all just work without a special
 * case. The migrations list still picks these up via
 * ``migrationSummaryOf`` (which already walks linear, fan-out and
 * graph shapes).
 */

export type SinkMode = "append" | "overwrite" | "upsert";
export type IfExists = "skip" | "drop" | "error";

/** RDBMS connector types that implement ``SchemaWriter``
 *  (``ensure_table``) — Phase VV / ADR-0066. ``auto_create_table``
 *  only does anything on these, so the form filters connections to
 *  this set. */
export const MIGRATION_SUPPORTED_TYPES = new Set([
  "postgres",
  "mysql",
  "sqlite",
]);

export interface MigrationFormData {
  sourceConnection: string;
  sourceQuery: string;
  sinkConnection: string;
  sinkTable: string;
  sinkMode: SinkMode;
  /** Required when ``sinkMode === "upsert"`` — comma-separated user
   *  input is split here, not on every keystroke. */
  keyColumns: string;
  /** Always ``true`` on the migration form — the whole point of
   *  this surface is the auto-create. Kept as a field so future
   *  variants (e.g. "Migration without auto-create") stay shaped
   *  the same. */
  autoCreateTable: boolean;
  ifExists: IfExists;
}

export const DEFAULT_MIGRATION_FORM: MigrationFormData = {
  sourceConnection: "",
  sourceQuery: "",
  sinkConnection: "",
  sinkTable: "",
  sinkMode: "overwrite",
  keyColumns: "",
  autoCreateTable: true,
  ifExists: "skip",
};

export interface MigrationFormErrors {
  sourceConnection?: string;
  sourceQuery?: string;
  sinkConnection?: string;
  sinkTable?: string;
  keyColumns?: string;
}

export function validateMigrationForm(
  form: MigrationFormData,
): MigrationFormErrors {
  const errs: MigrationFormErrors = {};
  if (!form.sourceConnection) errs.sourceConnection = "required";
  if (!form.sourceQuery.trim()) errs.sourceQuery = "required";
  if (!form.sinkConnection) errs.sinkConnection = "required";
  if (!form.sinkTable.trim()) errs.sinkTable = "required";
  if (form.sinkMode === "upsert") {
    const cols = splitKeyColumns(form.keyColumns);
    if (cols.length === 0) errs.keyColumns = "required";
  }
  return errs;
}

export function splitKeyColumns(text: string): string[] {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

/** Build the ``PipelineConfig`` JSON the server stores. The form
 *  data is closed over the named pipeline, so this function takes
 *  the name + form separately. */
export function buildMigrationConfig(
  name: string,
  form: MigrationFormData,
): Record<string, unknown> {
  const sink: Record<string, unknown> = {
    connection: form.sinkConnection,
    table: form.sinkTable.trim(),
    mode: form.sinkMode,
    auto_create_table: form.autoCreateTable,
  };
  // Only emit if_exists when it diverges from the default — keeps
  // the saved JSON minimal and matches what the builder writes.
  if (form.ifExists !== "skip") {
    sink.auto_create_if_exists = form.ifExists;
  }
  if (form.sinkMode === "upsert") {
    sink.key_columns = splitKeyColumns(form.keyColumns);
  }
  return {
    name,
    mode: "batch",
    source: {
      connection: form.sourceConnection,
      query: form.sourceQuery.trim(),
    },
    sink,
  };
}

/** Inverse of ``buildMigrationConfig`` — populate the form from a
 *  ``current_config_json`` the server returned. Returns ``null`` if
 *  the config doesn't look like a migration shape (e.g. graph mode
 *  or no auto-create sink); the caller routes the user to the
 *  generic builder in that case. */
export function parseMigrationConfig(
  config: Record<string, unknown> | null | undefined,
): MigrationFormData | null {
  if (!config || typeof config !== "object") return null;
  // Migrations are linear: ``source`` + single ``sink``. A graph or
  // fan-out config is too rich for this form; route the user to the
  // builder instead.
  if (config.graph) return null;
  if (Array.isArray(config.sinks) && config.sinks.length > 0) return null;

  const src = config.source as Record<string, unknown> | undefined;
  const snk = config.sink as Record<string, unknown> | undefined;
  if (!src || !snk) return null;
  if (snk.auto_create_table !== true) return null;

  const mode = typeof snk.mode === "string" ? snk.mode : "append";
  const keyColumns = Array.isArray(snk.key_columns)
    ? snk.key_columns.filter((c): c is string => typeof c === "string").join(", ")
    : "";
  return {
    sourceConnection: typeof src.connection === "string" ? src.connection : "",
    sourceQuery: typeof src.query === "string" ? src.query : "",
    sinkConnection: typeof snk.connection === "string" ? snk.connection : "",
    sinkTable: typeof snk.table === "string" ? snk.table : "",
    sinkMode:
      mode === "append" || mode === "overwrite" || mode === "upsert"
        ? mode
        : "append",
    keyColumns,
    autoCreateTable: true,
    ifExists:
      snk.auto_create_if_exists === "drop" ||
      snk.auto_create_if_exists === "error"
        ? (snk.auto_create_if_exists as IfExists)
        : "skip",
  };
}
