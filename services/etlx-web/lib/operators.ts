/**
 * Operator catalog — single source of truth for the Pipeline Builder.
 *
 * Each operator knows:
 *  - which "kind" it is (source | transform | sink) so the canvas can build
 *    a linear PipelineConfig from a node list.
 *  - a human label + Lucide icon + accent color (token-friendly).
 *  - a list of fields (rendered as the right-panel form).
 *
 * The shape mirrors core models in `etl_plugins.config.models` so the UI never
 * invents a knob the runtime can't honor. When a new connector or transform is
 * added in core, add it here too — no auto-introspection on the wire.
 */

import type { ComponentType } from "react";
import {
  CableIcon,
  Columns3Icon,
  CopyMinusIcon,
  CopyPlusIcon,
  DatabaseIcon,
  DatabaseZapIcon,
  EraserIcon,
  FileTextIcon,
  FilterIcon,
  GitMergeIcon,
  GlobeIcon,
  HardDriveIcon,
  LayersIcon,
  LeafIcon,
  RadioTowerIcon,
  ReplaceIcon,
  ShieldCheckIcon,
  SigmaIcon,
  TerminalIcon,
  WrenchIcon,
  type LucideProps,
} from "lucide-react";

// "call" was a linear-builder kind for fire-and-forget pipeline-to-pipeline
// triggers (ADR-0029). Graph-only mode (2026-05-26) surfaces those in the
// pipeline settings panel instead, so the operator kind is gone.
export type OperatorKind = "source" | "transform" | "sink";

interface FieldBase {
  key: string;
  label: string;
  help?: string;
  /** Marks the field with an asterisk and triggers an inline "required"
   *  warning when left empty. Connections + a transform's core input are
   *  the usual ones. */
  required?: boolean;
  /** Phase AAF (2026-05-29): conditional visibility. When set, the field
   *  is rendered only if ``nodeData[showWhen.field] === showWhen.equals``.
   *  Used to hide ``auto_create_if_exists`` unless ``auto_create_table``
   *  is on — the latter defaults to off, so leaving the if_exists
   *  select visible would be clutter for the 99% who don't auto-create. */
  showWhen?: { field: string; equals: unknown };
}

export type FieldDef =
  | (FieldBase & {
      kind: "string" | "number";
      placeholder?: string;
      multiline?: boolean;
      // Phase AAS follow-up 2 (2026-06-01) — operators can ship a
      // ``defaultValue`` that ``makeNode`` injects into the node's
      // ``data`` so the wire shape carries it without the user
      // having to touch the field.
      defaultValue?: string | number;
    })
  | (FieldBase & {
      // A table / collection name with introspected suggestions. Renders a
      // text input backed by a <datalist> of the selected connection's tables
      // (ADR-0033); free text is still allowed for tables the introspection
      // can't reach (permissions, other schemas).
      kind: "table";
      placeholder?: string;
    })
  | (FieldBase & {
      // DB-source read spec. Stores a SQL string (the connector's `query`) but
      // offers two ways to build it: raw SQL, or a point-and-click schema →
      // table → columns picker (ADR-0033) that compiles to `SELECT ... FROM`.
      kind: "sourceQuery";
      placeholder?: string;
    })
  | (FieldBase & {
      // Boolean toggle (Phase YY / ADR-0069, 2026-05-29). Renders a
      // checkbox. The wire shape is plain ``true`` / ``false`` — the
      // serializer drops the key when ``false`` so configs stay
      // minimal (no ``auto_create_table: false`` clutter on every
      // sink that didn't opt in).
      kind: "boolean";
      defaultValue?: boolean;
    })
  | (FieldBase & {
      kind: "select";
      options: { label: string; value: string }[];
      defaultValue?: string;
    })
  | (FieldBase & {
      kind: "json";
      placeholder?: string;
    })
  | (FieldBase & {
      // A list of column names. Renders a checklist of introspected columns
      // (ADR-0033) — upstream source columns for transforms, the sink table's
      // own columns for upsert keys — plus free-text add. Serializes to a
      // string[], identical wire shape to the old raw-JSON array field.
      kind: "columns";
    })
  | (FieldBase & {
      kind: "connection";
    })
  | (FieldBase & {
      // No-code key→value table. "rename": free-text new name; "cast": the
      // value is a type chosen from a dropdown. Serializes to a flat JSON
      // object, identical to what the old raw-JSON field produced.
      kind: "mapping";
      mappingKind: "rename" | "cast";
    })
  | (FieldBase & {
      // No-code condition builder for the filter transform. Generates the
      // Python expression the core's sandboxed filter expects, with a raw
      // "advanced" fallback. Stores a plain string (the expression).
      kind: "filter";
    })
  | (FieldBase & {
      // Picks another pipeline in the workspace (call-pipeline operator,
      // ADR-0029). Stores the target pipeline's id; persisted via the
      // pipeline_triggers API, not config_json.
      kind: "pipeline";
    })
  | (FieldBase & {
      // Inline Python source for the ``custom_python`` transform
      // (ADR-0041 I2). Lazy-loads Monaco so the bundle stays cheap. The
      // user's code must define a top-level ``transform(record)`` function;
      // the runtime compiles it once at build, executes it per record.
      kind: "pythonCode";
      placeholder?: string;
    })
  | (FieldBase & {
      // Raw SQL statement (Phase ADX, 2026-06-04). Same Monaco IDE as
      // ``pythonCode`` but pinned to the SQL grammar — used by the
      // ``sql_exec`` "Run SQL" node. Stores a plain string.
      kind: "sql";
      placeholder?: string;
    });

export interface OperatorSpec {
  /** Stable id — used as the React Flow node `type` + as the JSON config marker. */
  id: string;
  kind: OperatorKind;
  /** What the runtime stores as `type` (transform) or implies via connection (source/sink). */
  connectorType?: string;
  label: string;
  description: string;
  icon: ComponentType<LucideProps>;
  /** Hex accent used in nodes + palette pills. Token-aligned values only. */
  accent: string;
  /** Connection field shows ALL connections, not just ``connectorType`` matches.
   *  Used by the "Run SQL" step, which targets an arbitrary DB connection. */
  anyConnection?: boolean;
  /** Source/sink connectors that operate on unbounded streams (Kafka). A
   *  stream-mode pipeline only allows streaming source/sink; a batch-mode
   *  pipeline only allows non-streaming ones. Transforms/orchestration apply
   *  to both, so they leave this undefined. */
  streaming?: boolean;
  /** Accepts 2+ incoming edges in the graph builder (ADR-0041 G2/I1, Phase I).
   *  The "fan-in" guard in the graph canvas's edge validator is relaxed for
   *  these — join is the only node kind that takes ≥2 inputs today. */
  multiInput?: boolean;
  /** Graph-only operator (skipped by the linear-mode palette filter). Join
   *  and aggregate emit dedicated graph-node types, not transform wrappers,
   *  so they don't fit the linear `source → transform* → sink` shape. */
  graphOnly?: boolean;
  fields: FieldDef[];
}

/** Whether an operator may be added to a pipeline of the given mode.
 *  Only source/sink are mode-restricted; transforms + orchestration apply to both. */
export function operatorAllowedForMode(
  spec: OperatorSpec,
  mode: "batch" | "stream",
): boolean {
  if (spec.kind !== "source" && spec.kind !== "sink") return true;
  return mode === "stream" ? spec.streaming === true : spec.streaming !== true;
}

/** Look up the locale-aware label / description for an operator.
 *
 *  Phase L2 (2026-05-26 user request "각 Operator에 대한 설명 또한 한/영
 *  전환이 가능하도록"). Each operator declares its English label /
 *  description inline; the i18n table at ``lib/i18n/messages.ts``
 *  ships matching ``op.<id>.label`` / ``op.<id>.description`` keys
 *  for both languages. The helper does a typed lookup with safe
 *  fallback to the inline string so newly-added operators that haven't
 *  been translated yet still render (just untranslated) — no crash,
 *  no empty card.
 */
import type { Messages } from "@/lib/i18n/messages";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

export function getOperatorLabel(spec: OperatorSpec, t: Translate): string {
  const key = `op.${spec.id}.label` as keyof Messages;
  const v = t(key);
  // ``t()`` returns the key itself when missing — fall back to the
  // operator's inline English label in that case.
  return v === key ? spec.label : v;
}

export function getOperatorDescription(spec: OperatorSpec, t: Translate): string {
  const key = `op.${spec.id}.description` as keyof Messages;
  const v = t(key);
  return v === key ? spec.description : v;
}

// Incremental / backfill cursor (ADR-0039). Optional on RDBMS sources; enables
// the Backfill action to read a value range via the connector's read_since.
const CURSOR_COLUMN_FIELD: FieldDef = {
  key: "cursor_column",
  label: "Cursor column",
  kind: "string",
  placeholder: "updated_at",
  help: "Optional. A column the query returns (e.g. updated_at, id) used for incremental reads — enables the Backfill action over a value range.",
};

const SOURCES: OperatorSpec[] = [
  {
    id: "source:postgres",
    kind: "source",
    connectorType: "postgres",
    label: "Postgres",
    description: "Read rows from a PostgreSQL table via a SQL query.",
    icon: DatabaseIcon,
    accent: "#6366F1",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      {
        key: "query",
        label: "Read",
        kind: "sourceQuery",
        placeholder: "SELECT id, name, created_at FROM users",
      },
      { key: "chunk_size", label: "Chunk size", kind: "number", placeholder: "10000", defaultValue: 10000 },
      CURSOR_COLUMN_FIELD,
    ],
  },
  {
    id: "source:mysql",
    kind: "source",
    connectorType: "mysql",
    label: "MySQL",
    description: "Read rows from a MySQL table via a SQL query.",
    icon: DatabaseIcon,
    accent: "#06B6D4",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      { key: "query", label: "Read", kind: "sourceQuery" },
      { key: "chunk_size", label: "Chunk size", kind: "number", defaultValue: 10000 },
      CURSOR_COLUMN_FIELD,
    ],
  },
  {
    id: "source:sqlite",
    kind: "source",
    connectorType: "sqlite",
    label: "SQLite",
    description: "Read rows from a local SQLite database.",
    icon: HardDriveIcon,
    accent: "#10B981",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      { key: "query", label: "Read", kind: "sourceQuery" },
      CURSOR_COLUMN_FIELD,
    ],
  },
  {
    // Phase AAQ (2026-05-29) — analytical / column-store DB.
    id: "source:vertica",
    kind: "source",
    connectorType: "vertica",
    label: "Vertica",
    description: "Read rows from a Vertica analytical DB via a SQL query.",
    icon: DatabaseIcon,
    accent: "#0EA5E9",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      { key: "query", label: "Read", kind: "sourceQuery" },
      { key: "chunk_size", label: "Chunk size", kind: "number", defaultValue: 10000 },
      CURSOR_COLUMN_FIELD,
    ],
  },
  {
    // Phase AAQ (2026-05-29) — SQL Server / Azure SQL.
    id: "source:mssql",
    kind: "source",
    connectorType: "mssql",
    label: "SQL Server",
    description: "Read rows from a SQL Server / Azure SQL table.",
    icon: DatabaseIcon,
    accent: "#A78BFA",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      { key: "query", label: "Read", kind: "sourceQuery" },
      { key: "chunk_size", label: "Chunk size", kind: "number", defaultValue: 10000 },
      CURSOR_COLUMN_FIELD,
    ],
  },
  {
    id: "source:mongodb",
    kind: "source",
    connectorType: "mongodb",
    label: "MongoDB",
    description: "Read documents from a MongoDB collection (with filter / sort / projection).",
    icon: LeafIcon,
    accent: "#22C55E",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      {
        key: "query",
        label: "Collection",
        kind: "string",
        placeholder: "users",
        help: "Collection name within the connection's database.",
      },
      {
        key: "filter",
        label: "Filter",
        kind: "json",
        placeholder: '{"active": true}',
        help: "Mongo find filter as JSON. Leave blank or {} for all documents.",
      },
      {
        key: "projection",
        label: "Projection",
        kind: "json",
        placeholder: '{"name": 1, "_id": 0}',
        help: "JSON projection object. Leave blank to return full documents.",
      },
      {
        key: "limit",
        label: "Limit",
        kind: "number",
        placeholder: "0",
        help: "Cap on documents returned. 0 = unbounded.",
      },
    ],
  },
  {
    id: "source:s3",
    kind: "source",
    connectorType: "s3",
    label: "S3 object",
    description: "Read parquet/CSV/JSON from an S3 (or MinIO) bucket.",
    icon: CableIcon,
    accent: "#F59E0B",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      { key: "key", label: "Object key", kind: "string", placeholder: "exports/orders.parquet" },
      {
        key: "format",
        label: "Format",
        kind: "select",
        options: [
          { label: "parquet", value: "parquet" },
          { label: "csv", value: "csv" },
          { label: "jsonl", value: "jsonl" },
        ],
      },
    ],
  },
  {
    id: "source:kafka",
    kind: "source",
    connectorType: "kafka",
    label: "Kafka topic",
    description: "Stream-source records from a Kafka topic (use a stream-mode pipeline).",
    icon: RadioTowerIcon,
    accent: "#EC4899",
    streaming: true,
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      { key: "topic", label: "Topic", kind: "string", placeholder: "events.user_signup" },
      { key: "group_id", label: "Consumer group", kind: "string" },
      {
        key: "format",
        label: "Format",
        kind: "select",
        options: [
          { label: "json", value: "json" },
          { label: "avro", value: "avro" },
        ],
      },
    ],
  },
  {
    id: "source:http",
    kind: "source",
    connectorType: "http",
    label: "HTTP / REST",
    description: "Read JSON records from a REST endpoint (paginated GET).",
    icon: GlobeIcon,
    accent: "#8B5CF6",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      {
        key: "query",
        label: "Path",
        kind: "string",
        placeholder: "/v1/orders",
        help: "Path appended to the connection's base_url.",
      },
      {
        key: "records_field",
        label: "Records field",
        kind: "string",
        placeholder: "items",
        help: "Key in the JSON response object that holds the list of records. Leave default for top-level arrays.",
      },
      {
        key: "page_param",
        label: "Page parameter",
        kind: "string",
        placeholder: "page",
        help: "Query-param name for pagination. Leave blank for single-page fetches.",
      },
      {
        key: "params",
        label: "Static params",
        kind: "json",
        placeholder: '{"status": "active", "limit": 100}',
        help: "JSON object of query params sent on every request.",
      },
    ],
  },
  {
    // ADR-0042 follow-up (2026-05-26 user request "Run SQL 또한 Before
    // load가 아닌 그냥 SQL 수행을 위한 Operator로 SOURCE와 통합한 형태
    // 로 제공"). Backed by the new GRAPH_NODE_TYPE ``sql_exec`` — the
    // graph executor runs ``execute_statement`` on the named connection
    // and emits zero records. ``anyConnection`` so the user can target
    // any DB / SqlExecutor-capable connection in the workspace.
    id: "source:sql_exec",
    kind: "source",
    connectorType: "sql_exec",
    label: "Run SQL",
    description:
      "Execute a SQL statement against a connection (DDL, DELETE, MERGE…). Stands alone — no source/sink chain needed.",
    icon: DatabaseZapIcon,
    accent: "#FBBF24",
    anyConnection: true,
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      {
        key: "statement",
        label: "SQL statement",
        kind: "sql",
        required: true,
        placeholder: "DELETE FROM public.orders WHERE batch_date = '2026-05-21'",
        help: "Runs once when the pipeline reaches this node. Pure side effect — emits no records.",
      },
    ],
  },
];

const TRANSFORMS: OperatorSpec[] = [
  {
    id: "transform:rename",
    kind: "transform",
    connectorType: "rename",
    label: "Rename columns",
    description: "Rename keys on the record via a column → column mapping.",
    icon: ReplaceIcon,
    accent: "#FBBF24",
    fields: [
      {
        key: "mapping",
        label: "Mapping",
        kind: "mapping",
        mappingKind: "rename",
        help: "Rename a column to a new name. Add a row per column.",
      },
    ],
  },
  {
    id: "transform:cast",
    kind: "transform",
    connectorType: "cast",
    label: "Cast types",
    description: "Coerce columns to int / float / str / bool / timestamp.",
    icon: SigmaIcon,
    accent: "#FBBF24",
    fields: [
      {
        key: "columns",
        label: "Column → type",
        kind: "mapping",
        mappingKind: "cast",
        help: "Convert a column to a target type. Add a row per column.",
      },
    ],
  },
  {
    id: "transform:filter",
    kind: "transform",
    connectorType: "filter",
    label: "Filter rows",
    description: "Keep only rows where a sandboxed Python expression returns truthy.",
    icon: FilterIcon,
    accent: "#FBBF24",
    fields: [
      {
        key: "expr",
        label: "Conditions",
        kind: "filter",
        required: true,
        help: "Keep only rows matching every condition. Switch to Advanced for a raw Python expression (locals: data, metadata; no builtins).",
      },
    ],
  },
  {
    id: "transform:select",
    kind: "transform",
    connectorType: "select",
    label: "Select columns",
    description: "Keep only the listed columns; drop the rest.",
    icon: Columns3Icon,
    accent: "#FBBF24",
    fields: [
      {
        key: "columns",
        label: "Columns to keep",
        kind: "columns",
        required: true,
        help: "Tick the upstream columns to keep, or add by name.",
      },
    ],
  },
  {
    id: "transform:drop",
    kind: "transform",
    connectorType: "drop",
    label: "Drop columns",
    description: "Remove the listed columns; keep the rest.",
    icon: EraserIcon,
    accent: "#FBBF24",
    fields: [
      {
        key: "columns",
        label: "Columns to drop",
        kind: "columns",
        required: true,
        help: "Tick the upstream columns to remove, or add by name.",
      },
    ],
  },
  {
    id: "transform:add_constant",
    kind: "transform",
    connectorType: "add_constant",
    label: "Add constant",
    description: "Set a column to a constant value on every record.",
    icon: CopyPlusIcon,
    accent: "#FBBF24",
    fields: [
      {
        key: "column",
        label: "Column",
        kind: "string",
        required: true,
        placeholder: "source_system",
      },
      {
        key: "value",
        label: "Value",
        kind: "json",
        placeholder: '"crm"  (JSON: string, number, bool, null)',
        help: "JSON literal — string, number, boolean, or null.",
      },
    ],
  },
  {
    id: "transform:dedupe",
    kind: "transform",
    connectorType: "dedupe",
    label: "Deduplicate",
    description: "Drop records whose key columns were already seen in this run.",
    icon: CopyMinusIcon,
    accent: "#FBBF24",
    fields: [
      {
        key: "key_columns",
        label: "Key columns",
        kind: "columns",
        required: true,
        help: "Records with a repeated key tuple are dropped.",
      },
    ],
  },
  {
    id: "transform:python",
    kind: "transform",
    connectorType: "python",
    label: "Python callable",
    description: "Apply a user-supplied 'module:function' that returns a Record or None.",
    icon: TerminalIcon,
    accent: "#FBBF24",
    fields: [
      {
        key: "callable",
        label: "Callable",
        kind: "string",
        required: true,
        placeholder: "my_pkg.transforms:dedupe",
        help: "module:function — must be importable in the worker environment.",
      },
    ],
  },
  {
    // Inline Python source — the user writes the transform in the browser
    // (ADR-0041 I2). Same threat model as ``python`` (arbitrary in-process
    // execution), so Editor+ write + audit are the entire trust boundary
    // today; sandboxing plugs into the core's single ``custom_python``
    // execution seam without touching this UI.
    id: "transform:custom_python",
    kind: "transform",
    connectorType: "custom_python",
    label: "Custom Python (inline)",
    description: "Write a transform(record) function in the browser; runs in the worker per record.",
    icon: TerminalIcon,
    accent: "#FBBF24",
    fields: [
      {
        key: "code",
        label: "Python source",
        kind: "pythonCode",
        required: true,
        placeholder: "def transform(record):\n    return record",
        help: "Must define `transform(record) -> Record | None`. Runs in the worker; same trust model as the Python callable operator.",
      },
    ],
  },
  {
    // Fan-in operator (ADR-0041 G2/I1). Accepts 2+ incoming edges; merges
    // records on the named key columns. Input order = edge creation order
    // (first edge in = left input).
    id: "transform:join",
    kind: "transform",
    connectorType: "join",
    label: "Join",
    description: "Merge two or more inputs on matching key columns (inner / left / right / outer).",
    icon: GitMergeIcon,
    accent: "#FBBF24",
    multiInput: true,
    graphOnly: true,
    fields: [
      {
        key: "on",
        label: "Key columns",
        kind: "columns",
        required: true,
        help: "Join keys — each input must have all of these columns. First edge drawn = left input.",
      },
      {
        key: "how",
        label: "How",
        kind: "select",
        options: [
          { label: "inner", value: "inner" },
          { label: "left", value: "left" },
          { label: "right", value: "right" },
          { label: "outer", value: "outer" },
        ],
      },
    ],
  },
  {
    // Group-by + per-group aggregation (ADR-0041 G3). Takes one input, emits
    // one record per (group_by) tuple with the configured aggregations.
    id: "transform:aggregate",
    kind: "transform",
    connectorType: "aggregate",
    label: "Aggregate",
    description: "Group records by columns and compute count / sum / min / max / avg per group.",
    icon: LayersIcon,
    accent: "#FBBF24",
    graphOnly: true,
    fields: [
      {
        key: "group_by",
        label: "Group by",
        kind: "columns",
        help: "Group records by these column values; omit for a single global group.",
      },
      {
        key: "aggregations",
        label: "Aggregations (JSON)",
        kind: "json",
        required: true,
        placeholder: '[{"op":"sum","column":"amount","name":"total"},{"op":"count","name":"n"}]',
        help: "Array of {op, column?, name}. op = count | sum | min | max | avg. count may omit column.",
      },
    ],
  },
  {
    // Data-quality gate (ADR-0041 K1). Same expression contract as filter,
    // but a falsy outcome fails the run (default) or silently drops the
    // record — no silent bad data.
    id: "transform:assert",
    kind: "transform",
    connectorType: "assert",
    label: "Assertion",
    description: "Fail the run (or drop the row) when a data-quality condition isn't met.",
    icon: ShieldCheckIcon,
    accent: "#FBBF24",
    fields: [
      {
        key: "condition",
        label: "Condition",
        kind: "filter",
        required: true,
        help: "Records must satisfy every condition. Switch to Advanced for a raw Python expression (locals: data, metadata; no builtins).",
      },
      {
        key: "on_fail",
        label: "On failure",
        kind: "select",
        options: [
          { label: "Fail the run", value: "fail" },
          { label: "Drop the record", value: "drop" },
        ],
        help: "Fail = stop the run with this row's error. Drop = silently filter the offending row and keep going.",
      },
      {
        key: "message",
        label: "Failure message",
        kind: "string",
        placeholder: "amount must be non-negative",
        help: "Optional. Rendered into the run's error message when the assertion fails. Defaults to the condition text.",
      },
    ],
  },
];

const SINKS: OperatorSpec[] = [
  {
    id: "sink:postgres",
    kind: "sink",
    connectorType: "postgres",
    label: "Postgres",
    description: "Write records into a PostgreSQL table.",
    icon: DatabaseIcon,
    accent: "#4ADE80",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      { key: "table", label: "Table", kind: "table", placeholder: "schema.table" },
      {
        key: "mode",
        label: "Mode",
        kind: "select",
        defaultValue: "append",
        options: [
          { label: "append", value: "append" },
          { label: "overwrite", value: "overwrite" },
          { label: "upsert", value: "upsert" },
        ],
      },
      {
        key: "key_columns",
        label: "Key columns",
        kind: "columns",
        help: "Required for upsert mode. Ticks the destination table's columns.",
      },
      {
        key: "pre_sql",
        label: "Pre-write SQL (atomic)",
        kind: "sql",
        placeholder: "DELETE FROM public.orders WHERE batch_date = '2026-05-21'",
        help: "Runs inside the write transaction, before insert. DELETE + insert commit together — atomic, idempotent re-runs (no duplicates, no data loss on failure).",
      },
      {
        key: "auto_create_table",
        label: "Create table if missing",
        kind: "boolean",
        help: "Read the source schema (and any rename/cast/add_constant transforms) and CREATE TABLE on this sink before the first write. Cross-DB types are translated automatically (BIGINT → INT/INTEGER, TIMESTAMPTZ → DATETIME/TEXT, etc.).",
      },
      {
        key: "auto_create_if_exists",
        label: "If table exists",
        kind: "select",
        showWhen: { field: "auto_create_table", equals: true },
        options: [
          { label: "skip — use existing table as-is", value: "skip" },
          { label: "drop — DROP and recreate (snapshot rebuild)", value: "drop" },
          { label: "error — refuse to clobber", value: "error" },
        ],
        help: "‘drop’ is the right choice for nightly snapshot rebuilds where the source schema may evolve — stale columns/rows are wiped each run. Default: skip.",
      },
    ],
  },
  {
    id: "sink:mysql",
    kind: "sink",
    connectorType: "mysql",
    label: "MySQL",
    description: "Write records into a MySQL table.",
    icon: DatabaseIcon,
    accent: "#4ADE80",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      { key: "table", label: "Table", kind: "table" },
      {
        key: "mode",
        label: "Mode",
        kind: "select",
        defaultValue: "append",
        options: [
          { label: "append", value: "append" },
          { label: "overwrite", value: "overwrite" },
          { label: "upsert", value: "upsert" },
        ],
      },
      { key: "key_columns", label: "Key columns", kind: "columns" },
      {
        key: "pre_sql",
        label: "Pre-write SQL (atomic)",
        kind: "sql",
        placeholder: "DELETE FROM orders WHERE batch_date = '2026-05-21'",
        help: "Runs inside the write transaction, before insert (use DELETE, not TRUNCATE, on MySQL). DELETE + insert commit together — atomic, idempotent re-runs.",
      },
      {
        key: "auto_create_table",
        label: "Create table if missing",
        kind: "boolean",
        help: "Read the source schema (and any rename/cast/add_constant transforms) and CREATE TABLE on this sink before the first write. Cross-DB types are translated automatically (postgres BIGINT → mysql BIGINT, TIMESTAMPTZ → DATETIME, etc.).",
      },
      {
        key: "auto_create_if_exists",
        label: "If table exists",
        kind: "select",
        showWhen: { field: "auto_create_table", equals: true },
        options: [
          { label: "skip — use existing table as-is", value: "skip" },
          { label: "drop — DROP and recreate (snapshot rebuild)", value: "drop" },
          { label: "error — refuse to clobber", value: "error" },
        ],
        help: "‘drop’ is the right choice for nightly snapshot rebuilds where the source schema may evolve — stale columns/rows are wiped each run. Default: skip.",
      },
    ],
  },
  {
    id: "sink:sqlite",
    kind: "sink",
    connectorType: "sqlite",
    label: "SQLite",
    description: "Write records into a local SQLite database.",
    icon: HardDriveIcon,
    accent: "#4ADE80",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      { key: "table", label: "Table", kind: "table" },
      {
        key: "mode",
        label: "Mode",
        kind: "select",
        defaultValue: "append",
        options: [
          { label: "append", value: "append" },
          { label: "overwrite", value: "overwrite" },
          { label: "upsert", value: "upsert" },
        ],
      },
      {
        key: "pre_sql",
        label: "Pre-write SQL (atomic)",
        kind: "sql",
        placeholder: "DELETE FROM orders WHERE batch_date = '2026-05-21'",
        help: "Runs inside the write transaction, before insert. DELETE + insert commit together — atomic, idempotent re-runs.",
      },
      {
        key: "auto_create_table",
        label: "Create table if missing",
        kind: "boolean",
        help: "Read the source schema (and any rename/cast/add_constant transforms) and CREATE TABLE on this sink before the first write. Useful for postgres→sqlite or mysql→sqlite migrations — types collapse to sqlite's affinity rules automatically.",
      },
      {
        key: "auto_create_if_exists",
        label: "If table exists",
        kind: "select",
        options: [
          { label: "skip — use existing table as-is", value: "skip" },
          { label: "drop — DROP and recreate (snapshot rebuild)", value: "drop" },
          { label: "error — refuse to clobber", value: "error" },
        ],
        help: "Only applied when ‘Create table if missing’ is on. ‘drop’ rebuilds the file's schema each run — good for daily snapshot caches.",
      },
    ],
  },
  {
    // Phase AAQ (2026-05-29) — Vertica analytical DB sink.
    id: "sink:vertica",
    kind: "sink",
    connectorType: "vertica",
    label: "Vertica",
    description: "Write records into a Vertica table.",
    icon: DatabaseIcon,
    accent: "#0EA5E9",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      { key: "table", label: "Table", kind: "table", placeholder: "schema.table" },
      {
        key: "mode",
        label: "Mode",
        kind: "select",
        defaultValue: "append",
        options: [
          { label: "append", value: "append" },
          { label: "overwrite", value: "overwrite" },
          { label: "upsert", value: "upsert" },
        ],
      },
      {
        key: "key_columns",
        label: "Key columns",
        kind: "columns",
        help: "Required for upsert mode. Used as the MERGE join keys.",
      },
      {
        key: "pre_sql",
        label: "Pre-write SQL (atomic)",
        kind: "sql",
        placeholder: "DELETE FROM public.orders WHERE batch_date = '2026-05-21'",
      },
      {
        key: "auto_create_table",
        label: "Create table if missing",
        kind: "boolean",
        help: "Read the source schema and CREATE TABLE on this sink. Cross-DB types translate automatically — JSON collapses to LONG VARCHAR.",
      },
      {
        key: "auto_create_if_exists",
        label: "If table exists",
        kind: "select",
        showWhen: { field: "auto_create_table", equals: true },
        options: [
          { label: "skip — use existing table as-is", value: "skip" },
          { label: "drop — DROP and recreate (snapshot rebuild)", value: "drop" },
          { label: "error — refuse to clobber", value: "error" },
        ],
        help: "‘drop’ is the right choice for nightly snapshot rebuilds.",
      },
    ],
  },
  {
    // Phase AAQ (2026-05-29) — SQL Server / Azure SQL sink.
    id: "sink:mssql",
    kind: "sink",
    connectorType: "mssql",
    label: "SQL Server",
    description: "Write records into a SQL Server / Azure SQL table.",
    icon: DatabaseIcon,
    accent: "#A78BFA",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      { key: "table", label: "Table", kind: "table", placeholder: "dbo.orders" },
      {
        key: "mode",
        label: "Mode",
        kind: "select",
        defaultValue: "append",
        options: [
          { label: "append", value: "append" },
          { label: "overwrite", value: "overwrite" },
          { label: "upsert", value: "upsert" },
        ],
      },
      {
        key: "key_columns",
        label: "Key columns",
        kind: "columns",
        help: "Required for upsert mode. Used as MERGE join keys; promoted to PRIMARY KEY when the destination is auto-created.",
      },
      {
        key: "pre_sql",
        label: "Pre-write SQL (atomic)",
        kind: "sql",
        placeholder: "DELETE FROM dbo.orders WHERE batch_date = '2026-05-21'",
      },
      {
        key: "auto_create_table",
        label: "Create table if missing",
        kind: "boolean",
        help: "Cross-DB types translate automatically — TEXT → NVARCHAR(MAX), TIMESTAMPTZ → DATETIME2, BOOLEAN → BIT.",
      },
      {
        key: "auto_create_if_exists",
        label: "If table exists",
        kind: "select",
        showWhen: { field: "auto_create_table", equals: true },
        options: [
          { label: "skip — use existing table as-is", value: "skip" },
          { label: "drop — DROP and recreate (snapshot rebuild)", value: "drop" },
          { label: "error — refuse to clobber", value: "error" },
        ],
        help: "‘drop’ is the right choice for nightly snapshot rebuilds.",
      },
    ],
  },
  {
    id: "sink:mongodb",
    kind: "sink",
    connectorType: "mongodb",
    label: "MongoDB",
    description: "Write documents into a MongoDB collection (append / overwrite / upsert).",
    icon: LeafIcon,
    accent: "#4ADE80",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      { key: "table", label: "Collection", kind: "table", placeholder: "users" },
      {
        key: "mode",
        label: "Mode",
        kind: "select",
        defaultValue: "append",
        options: [
          { label: "append", value: "append" },
          { label: "overwrite", value: "overwrite" },
          { label: "upsert", value: "upsert" },
        ],
      },
      {
        key: "key_columns",
        label: "Key fields",
        kind: "json",
        placeholder: '["_id"]',
        help: "Required for upsert mode. Each document must carry every key field.",
      },
    ],
  },
  {
    id: "sink:s3",
    kind: "sink",
    connectorType: "s3",
    label: "S3 object",
    description: "Write records to an S3 (or MinIO) object as parquet/CSV/JSON.",
    icon: CableIcon,
    accent: "#4ADE80",
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      { key: "key", label: "Object key", kind: "string", placeholder: "exports/out.parquet" },
      {
        key: "format",
        label: "Format",
        kind: "select",
        defaultValue: "parquet",
        options: [
          { label: "parquet", value: "parquet" },
          { label: "csv", value: "csv" },
          { label: "jsonl", value: "jsonl" },
        ],
      },
    ],
  },
  {
    id: "sink:kafka",
    kind: "sink",
    connectorType: "kafka",
    label: "Kafka topic",
    description: "Stream-sink records to a Kafka topic.",
    icon: RadioTowerIcon,
    accent: "#4ADE80",
    streaming: true,
    fields: [
      { key: "connection", label: "Connection", kind: "connection", required: true },
      { key: "topic", label: "Topic", kind: "string" },
      {
        key: "format",
        label: "Format",
        kind: "select",
        options: [
          { label: "json", value: "json" },
          { label: "avro", value: "avro" },
        ],
      },
    ],
  },
];

// Conditional routing (ADR-0027): every batch sink can carry an optional
// `when` predicate. Records route to the first sink whose condition matches;
// sinks with no condition catch the rest. Reuses the no-code filter builder
// since `when` is the same sandboxed Python expression as the filter transform.
// Excluded from Kafka — stream routing isn't supported.
const SINK_ROUTING_FIELD: FieldDef = {
  key: "when",
  label: "Routing condition",
  kind: "filter",
  help: "Optional. Only records matching this condition are written to this sink. The first matching sink wins; sinks with no condition receive everything that matched no other sink. Leave blank to receive all records.",
};
for (const s of SINKS) {
  if (s.connectorType !== "kafka") {
    s.fields = [...s.fields, SINK_ROUTING_FIELD];
  }
}

export const OPERATORS: OperatorSpec[] = [...SOURCES, ...TRANSFORMS, ...SINKS];

/** Sub-category within a kind — used to group a long palette (Airflow-style). */
export function operatorCategory(spec: OperatorSpec): string {
  if (spec.kind === "transform") {
    switch (spec.connectorType) {
      case "filter":
      case "dedupe":
        return "Rows";
      case "python":
        return "Code";
      case "sql_exec":
        return "Database";
      default:
        return "Columns";
    }
  }
  // source / sink — group by connector family
  switch (spec.connectorType) {
    case "postgres":
    case "mysql":
    case "sqlite":
      return "Databases";
    case "mongodb":
      return "NoSQL";
    case "s3":
      return "Object storage";
    case "kafka":
      return "Streaming";
    case "http":
      return "HTTP / API";
    default:
      return "Other";
  }
}

export const OPERATOR_GROUPS: { kind: OperatorKind; label: string; specs: OperatorSpec[] }[] = [
  { kind: "source", label: "Sources", specs: SOURCES },
  { kind: "transform", label: "Transforms", specs: TRANSFORMS },
  { kind: "sink", label: "Sinks", specs: SINKS },
];

/** Operators grouped kind → category → specs, for a collapsible palette. */
export interface OperatorCategoryGroup {
  category: string;
  specs: OperatorSpec[];
}
export interface OperatorKindGroup {
  kind: OperatorKind;
  label: string;
  categories: OperatorCategoryGroup[];
}

export const OPERATOR_KIND_GROUPS: OperatorKindGroup[] = OPERATOR_GROUPS.map((g) => {
  const byCategory = new Map<string, OperatorSpec[]>();
  for (const spec of g.specs) {
    const cat = operatorCategory(spec);
    const list = byCategory.get(cat) ?? [];
    list.push(spec);
    byCategory.set(cat, list);
  }
  return {
    kind: g.kind,
    label: g.label,
    categories: [...byCategory.entries()].map(([category, specs]) => ({ category, specs })),
  };
});

export function findOperator(id: string): OperatorSpec | undefined {
  return OPERATORS.find((op) => op.id === id);
}

export const OPERATOR_KIND_ACCENT: Record<OperatorKind, string> = {
  source: "#6366F1",
  transform: "#FBBF24",
  sink: "#4ADE80",
};

export const KIND_ICON: Record<OperatorKind, ComponentType<LucideProps>> = {
  source: DatabaseIcon,
  transform: WrenchIcon,
  sink: FileTextIcon,
};
