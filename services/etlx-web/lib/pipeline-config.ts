/**
 * Builder <-> PipelineConfig serialization.
 *
 * Core pipelines are linear: one source → N transforms → one sink. The visual
 * builder honors that constraint by laying nodes out in a single horizontal
 * row; this module converts back and forth between the React Flow node list
 * and the JSON the API expects.
 */

import { findOperator, OPERATORS, type OperatorSpec } from "./operators";

export interface BuilderNode {
  id: string;
  operatorId: string;
  data: Record<string, unknown>;
}

export interface RetrySettings {
  enabled: boolean;
  max_attempts: number;
  backoff: "fixed" | "exponential";
  initial_delay_seconds: number;
}

export interface DlqSettings {
  enabled: boolean;
  connection: string;
  table: string;
  topic: string;
  mode: "append" | "overwrite" | "upsert";
}

export interface BuilderState {
  nodes: BuilderNode[];
  retry: RetrySettings;
  dlq: DlqSettings;
}

export const DEFAULT_RETRY: RetrySettings = {
  enabled: false,
  max_attempts: 3,
  backoff: "exponential",
  initial_delay_seconds: 5,
};

export const DEFAULT_DLQ: DlqSettings = {
  enabled: false,
  connection: "",
  table: "",
  topic: "",
  mode: "append",
};

export interface PipelineConfigJson {
  name: string;
  mode: "batch" | "stream";
  source: { connection: string; [k: string]: unknown };
  transforms: { type: string; [k: string]: unknown }[];
  sink: { connection: string; [k: string]: unknown };
  retry?: {
    max_attempts: number;
    backoff: string;
    initial_delay_seconds: number;
  } | null;
  dlq?: {
    connection: string;
    table?: string | null;
    topic?: string | null;
    mode: string;
  } | null;
  [k: string]: unknown;
}

const KIND_ORDER: Record<OperatorSpec["kind"], number> = {
  source: 0,
  transform: 1,
  sink: 2,
};

export function blankBuilder(): BuilderState {
  return {
    nodes: [makeNode("source:postgres"), makeNode("sink:postgres")],
    retry: { ...DEFAULT_RETRY },
    dlq: { ...DEFAULT_DLQ },
  };
}

let _counter = 0;
function nextId(prefix: string): string {
  _counter += 1;
  return `${prefix}-${Date.now().toString(36)}-${_counter}`;
}

export function makeNode(operatorId: string): BuilderNode {
  const spec = findOperator(operatorId);
  if (!spec) throw new Error(`Unknown operator ${operatorId}`);
  return {
    id: nextId(spec.kind),
    operatorId,
    data: {},
  };
}

export function reorderNodes(nodes: BuilderNode[]): BuilderNode[] {
  return [...nodes].sort((a, b) => {
    const ka = findOperator(a.operatorId)?.kind ?? "transform";
    const kb = findOperator(b.operatorId)?.kind ?? "transform";
    return KIND_ORDER[ka] - KIND_ORDER[kb];
  });
}

export function serialize(
  state: BuilderState,
  meta: { name: string; mode?: "batch" | "stream" },
): PipelineConfigJson {
  const sorted = reorderNodes(state.nodes);
  const source = sorted.find(
    (n) => findOperator(n.operatorId)?.kind === "source",
  );
  const sink = sorted.find((n) => findOperator(n.operatorId)?.kind === "sink");
  const transforms = sorted.filter(
    (n) => findOperator(n.operatorId)?.kind === "transform",
  );

  if (!source) throw new Error("Pipeline needs a source operator.");
  if (!sink) throw new Error("Pipeline needs a sink operator.");

  const config: PipelineConfigJson = {
    name: meta.name,
    mode: meta.mode ?? "batch",
    source: {
      connection: "",
      ...source.data,
    } as PipelineConfigJson["source"],
    transforms: transforms.map((t) => ({
      type: findOperator(t.operatorId)?.connectorType ?? "unknown",
      ...t.data,
    })),
    sink: {
      connection: "",
      ...sink.data,
    } as PipelineConfigJson["sink"],
  };

  if (state.retry.enabled) {
    config.retry = {
      max_attempts: state.retry.max_attempts,
      backoff: state.retry.backoff,
      initial_delay_seconds: state.retry.initial_delay_seconds,
    };
  }
  if (state.dlq.enabled && state.dlq.connection) {
    const dlq: PipelineConfigJson["dlq"] = {
      connection: state.dlq.connection,
      mode: state.dlq.mode,
    };
    // Core DlqConfig stores either `table` (batch sink) or `topic` (stream
    // sink). Send whichever the user filled; both populated is unusual but
    // harmless — the core's runtime picks the relevant one per sink kind.
    if (state.dlq.table) dlq.table = state.dlq.table;
    if (state.dlq.topic) dlq.topic = state.dlq.topic;
    config.dlq = dlq;
  }

  return config;
}

/**
 * Rebuild a BuilderState from a stored PipelineConfig JSON.
 *
 * `connections` lets us recover the source/sink connector type: the config
 * only stores the connection *name* (the worker derives the type from the
 * Connection row), so without this lookup every reloaded source/sink would
 * fall back to Postgres — showing the wrong node + fields for an S3 or Mongo
 * pipeline.
 */
export function deserialize(
  config: PipelineConfigJson | null,
  connections: { name: string; type: string }[] = [],
): BuilderState {
  if (!config || !config.source || !config.sink) return blankBuilder();

  const typeByName = new Map(connections.map((c) => [c.name, c.type]));

  const source = OPERATORS.find(
    (op) =>
      op.kind === "source" &&
      op.connectorType ===
        guessConnectorType(config.source.connection, config.source, typeByName),
  );
  const sink = OPERATORS.find(
    (op) =>
      op.kind === "sink" &&
      op.connectorType ===
        guessConnectorType(config.sink.connection, config.sink, typeByName),
  );

  const nodes: BuilderNode[] = [];
  nodes.push({
    id: nextId("source"),
    operatorId: source?.id ?? "source:postgres",
    data: stripType({ ...config.source }),
  });
  for (const t of config.transforms ?? []) {
    const spec = OPERATORS.find(
      (op) => op.kind === "transform" && op.connectorType === t.type,
    );
    if (!spec) continue;
    nodes.push({
      id: nextId("transform"),
      operatorId: spec.id,
      data: stripType({ ...t }),
    });
  }
  nodes.push({
    id: nextId("sink"),
    operatorId: sink?.id ?? "sink:postgres",
    data: stripType({ ...config.sink }),
  });

  const retry: RetrySettings = config.retry
    ? {
        enabled: true,
        max_attempts: config.retry.max_attempts,
        backoff:
          config.retry.backoff === "fixed" ? "fixed" : "exponential",
        initial_delay_seconds: config.retry.initial_delay_seconds,
      }
    : { ...DEFAULT_RETRY };

  const dlq: DlqSettings = config.dlq
    ? {
        enabled: true,
        connection: config.dlq.connection,
        table: config.dlq.table ?? "",
        topic: config.dlq.topic ?? "",
        mode:
          config.dlq.mode === "overwrite" || config.dlq.mode === "upsert"
            ? config.dlq.mode
            : "append",
      }
    : { ...DEFAULT_DLQ };

  return { nodes, retry, dlq };
}

/**
 * Source/sink configs don't store a connector type — the worker derives it
 * from the referenced Connection. For the UI we still need to pick a node
 * icon; if there's a hint, use it, otherwise fall back to the first source.
 */
function guessConnectorType(
  connection: string | undefined,
  raw: Record<string, unknown>,
  typeByName: Map<string, string>,
): string {
  if (typeof raw.type === "string") return raw.type;
  // Use a `kind`/`__type` annotation if the UI stored one before saving.
  if (typeof raw.__connector === "string") return raw.__connector;
  // Recover the connector type from the referenced Connection.
  if (connection && typeByName.has(connection)) {
    return typeByName.get(connection)!;
  }
  return "postgres";
}

function stripType<T extends Record<string, unknown>>(o: T): T {
  const copy = { ...o };
  delete (copy as Record<string, unknown>).type;
  delete (copy as Record<string, unknown>).__connector;
  return copy;
}
