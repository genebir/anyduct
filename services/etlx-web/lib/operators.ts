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
  EraserIcon,
  FileTextIcon,
  FilterIcon,
  GlobeIcon,
  HardDriveIcon,
  LeafIcon,
  RadioTowerIcon,
  ReplaceIcon,
  SigmaIcon,
  TerminalIcon,
  WorkflowIcon,
  WrenchIcon,
  type LucideProps,
} from "lucide-react";

export type OperatorKind = "source" | "transform" | "sink" | "call";

interface FieldBase {
  key: string;
  label: string;
  help?: string;
  /** Marks the field with an asterisk and triggers an inline "required"
   *  warning when left empty. Connections + a transform's core input are
   *  the usual ones. */
  required?: boolean;
}

export type FieldDef =
  | (FieldBase & {
      kind: "string" | "number";
      placeholder?: string;
      multiline?: boolean;
    })
  | (FieldBase & {
      kind: "select";
      options: { label: string; value: string }[];
    })
  | (FieldBase & {
      kind: "json";
      placeholder?: string;
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
  fields: FieldDef[];
}

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
        label: "SQL query",
        kind: "string",
        multiline: true,
        placeholder: "SELECT id, name, created_at FROM users",
      },
      { key: "chunk_size", label: "Chunk size", kind: "number", placeholder: "10000" },
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
      { key: "query", label: "SQL query", kind: "string", multiline: true },
      { key: "chunk_size", label: "Chunk size", kind: "number" },
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
      { key: "query", label: "SQL query", kind: "string", multiline: true },
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
        kind: "json",
        required: true,
        placeholder: '["id", "name"]',
        help: "JSON array of column names to keep.",
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
        kind: "json",
        required: true,
        placeholder: '["secret", "_internal"]',
        help: "JSON array of column names to remove.",
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
        kind: "json",
        required: true,
        placeholder: '["id"]',
        help: "JSON array — records with a repeated key tuple are dropped.",
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
      { key: "table", label: "Table", kind: "string", placeholder: "schema.table" },
      {
        key: "mode",
        label: "Mode",
        kind: "select",
        options: [
          { label: "append", value: "append" },
          { label: "overwrite", value: "overwrite" },
          { label: "upsert", value: "upsert" },
        ],
      },
      {
        key: "key_columns",
        label: "Key columns",
        kind: "json",
        placeholder: '["id"]',
        help: "Required for upsert mode.",
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
      { key: "table", label: "Table", kind: "string" },
      {
        key: "mode",
        label: "Mode",
        kind: "select",
        options: [
          { label: "append", value: "append" },
          { label: "overwrite", value: "overwrite" },
          { label: "upsert", value: "upsert" },
        ],
      },
      { key: "key_columns", label: "Key columns", kind: "json" },
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
      { key: "table", label: "Table", kind: "string" },
      {
        key: "mode",
        label: "Mode",
        kind: "select",
        options: [
          { label: "append", value: "append" },
          { label: "overwrite", value: "overwrite" },
          { label: "upsert", value: "upsert" },
        ],
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
      { key: "table", label: "Collection", kind: "string", placeholder: "users" },
      {
        key: "mode",
        label: "Mode",
        kind: "select",
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

const CALLS: OperatorSpec[] = [
  {
    id: "call:pipeline",
    kind: "call",
    label: "Call pipeline",
    description: "On success, trigger another pipeline (fire-and-forget).",
    icon: WorkflowIcon,
    accent: "#A78BFA",
    fields: [
      {
        key: "pipeline_id",
        label: "Target pipeline",
        kind: "pipeline",
        required: true,
        help: "Runs after this pipeline succeeds. Cycles are skipped automatically.",
      },
    ],
  },
];

export const OPERATORS: OperatorSpec[] = [...SOURCES, ...TRANSFORMS, ...SINKS, ...CALLS];

/** Sub-category within a kind — used to group a long palette (Airflow-style). */
export function operatorCategory(spec: OperatorSpec): string {
  if (spec.kind === "call") return "Orchestration";
  if (spec.kind === "transform") {
    switch (spec.connectorType) {
      case "filter":
      case "dedupe":
        return "Rows";
      case "python":
        return "Code";
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
  { kind: "call", label: "Orchestration", specs: CALLS },
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
  call: "#A78BFA",
};

export const KIND_ICON: Record<OperatorKind, ComponentType<LucideProps>> = {
  source: DatabaseIcon,
  transform: WrenchIcon,
  sink: FileTextIcon,
  call: WorkflowIcon,
};
