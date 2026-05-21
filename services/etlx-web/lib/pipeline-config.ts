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

export type Engine = "local" | "spark";

export interface PipelineConfigJson {
  name: string;
  mode: "batch" | "stream";
  // Execution backend (ADR-0031). "local" = row-streaming (default); "spark"
  // compiles the DAG to Spark for distributed/TB-scale runs.
  engine?: Engine;
  // Single-task shape. Optional so DAG/graph configs (which omit the top-level
  // source) still type-check.
  source?: { connection: string; [k: string]: unknown };
  transforms?: { type: string; [k: string]: unknown }[];
  // Exactly one of `sink` (single) / `sinks` (fan-out) is populated — mirrors
  // core PipelineConfig (ADR-0026). The server stores `sink: null` + `sinks`
  // for fan-out, so both fields are optional/nullable here.
  sink?: { connection: string; [k: string]: unknown } | null;
  sinks?: { connection: string; [k: string]: unknown }[];
  // Dataflow graph shape (ADR-0030).
  graph?: GraphConfigJson;
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
  call: 3,
};

/** Target pipeline ids from call-pipeline nodes (ADR-0029). These persist via
 *  the pipeline_triggers API, not config_json. */
export function callTargets(nodes: BuilderNode[]): string[] {
  const ids: string[] = [];
  for (const n of nodes) {
    if (findOperator(n.operatorId)?.kind !== "call") continue;
    const id = n.data.pipeline_id;
    if (typeof id === "string" && id) ids.push(id);
  }
  return [...new Set(ids)];
}

/** Build one call-pipeline node for a target pipeline id. */
export function makeCallNode(pipelineId: string): BuilderNode {
  return { id: nextId("call"), operatorId: "call:pipeline", data: { pipeline_id: pipelineId } };
}

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

/** True if an operator can't be pushed down to Spark (ADR-0031): the arbitrary
 *  `python` transform has no Spark equivalent. Such pipelines must run `local`. */
export function isSparkUnsupported(operatorId: string): boolean {
  const op = findOperator(operatorId);
  return op?.kind === "transform" && op.connectorType === "python";
}

export function serialize(
  state: BuilderState,
  meta: { name: string; mode?: "batch" | "stream"; engine?: Engine },
): PipelineConfigJson {
  const sorted = reorderNodes(state.nodes);
  const source = sorted.find(
    (n) => findOperator(n.operatorId)?.kind === "source",
  );
  const sinks = sorted.filter((n) => findOperator(n.operatorId)?.kind === "sink");
  const transforms = sorted.filter(
    (n) => findOperator(n.operatorId)?.kind === "transform",
  );

  if (!source) throw new Error("Pipeline needs a source operator.");
  if (sinks.length === 0) throw new Error("Pipeline needs a sink operator.");

  const config: PipelineConfigJson = {
    name: meta.name,
    mode: meta.mode ?? "batch",
    ...(meta.engine && meta.engine !== "local" ? { engine: meta.engine } : {}),
    source: {
      connection: "",
      ...source.data,
    } as PipelineConfigJson["source"],
    transforms: transforms.map((t) => ({
      type: findOperator(t.operatorId)?.connectorType ?? "unknown",
      ...t.data,
    })),
  };

  // One sink → `sink` (legacy/linear), many → `sinks[]` (fan-out, ADR-0026).
  if (sinks.length === 1) {
    config.sink = {
      connection: "",
      ...sinks[0].data,
    } as NonNullable<PipelineConfigJson["sink"]>;
  } else {
    config.sinks = sinks.map(
      (s) => ({ connection: "", ...s.data }) as { connection: string },
    );
  }

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
  // Normalise to a sink list: prefer `sinks[]` (fan-out), fall back to the
  // single `sink`. A config with neither is unbuildable → blank.
  if (!config || !config.source) return blankBuilder();
  const srcCfg = config.source;
  const sinkCfgs =
    config.sinks && config.sinks.length > 0
      ? config.sinks
      : config.sink
        ? [config.sink]
        : [];
  if (sinkCfgs.length === 0) return blankBuilder();

  const typeByName = new Map(connections.map((c) => [c.name, c.type]));

  const source = OPERATORS.find(
    (op) =>
      op.kind === "source" &&
      op.connectorType === guessConnectorType(srcCfg.connection, srcCfg, typeByName),
  );

  const nodes: BuilderNode[] = [];
  nodes.push({
    id: nextId("source"),
    operatorId: source?.id ?? "source:postgres",
    data: stripType({ ...srcCfg }),
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
  for (const sinkCfg of sinkCfgs) {
    const sink = OPERATORS.find(
      (op) =>
        op.kind === "sink" &&
        op.connectorType ===
          guessConnectorType(sinkCfg.connection, sinkCfg, typeByName),
    );
    nodes.push({
      id: nextId("sink"),
      operatorId: sink?.id ?? "sink:postgres",
      data: stripType({ ...sinkCfg }),
    });
  }

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

/* ───────────────────────────────────────────────────────────────────────────
   Dataflow graph builder (ADR-0030).

   Free-form operator graph: nodes (source/transform/sink) connected by edges
   the user draws, with an optional per-edge `when` predicate for branching.
   Serializes to PipelineConfig.graph; the core runs it as a record-flow DAG.
   ─────────────────────────────────────────────────────────────────────────── */

export interface GraphBuilderNode {
  id: string;
  operatorId: string;
  data: Record<string, unknown>;
  position: { x: number; y: number };
}

export interface GraphBuilderEdge {
  id: string;
  source: string; // node id
  target: string; // node id
  when?: string; // predicate; empty/undefined = unconditional
}

export interface GraphBuilderState {
  nodes: GraphBuilderNode[];
  edges: GraphBuilderEdge[];
}

export interface GraphConfigJson {
  nodes: { id: string; type: string; [k: string]: unknown }[];
  edges: { from_node: string; to_node: string; when?: string | null }[];
}

export function isGraphConfig(
  config: PipelineConfigJson | null,
): config is PipelineConfigJson & { graph: GraphConfigJson } {
  return Boolean(config && (config as { graph?: unknown }).graph);
}

export function blankGraph(): GraphBuilderState {
  const src = makeNode("source:postgres");
  const snk = makeNode("sink:postgres");
  return {
    nodes: [
      { id: src.id, operatorId: src.operatorId, data: {}, position: { x: 0, y: 80 } },
      { id: snk.id, operatorId: snk.operatorId, data: {}, position: { x: 360, y: 80 } },
    ],
    edges: [{ id: nextId("edge"), source: src.id, target: snk.id }],
  };
}

export function makeGraphNode(operatorId: string, position: { x: number; y: number }): GraphBuilderNode {
  const n = makeNode(operatorId);
  return { id: n.id, operatorId: n.operatorId, data: {}, position };
}

export function nextEdgeId(): string {
  return nextId("edge");
}

/** Client-side graph validation mirroring the server's GraphConfig rules
 *  (ADR-0030 v1 tree). Returns human-readable problems; empty = valid to save. */
export function validateGraph(state: GraphBuilderState): string[] {
  const issues: string[] = [];
  const sources = state.nodes.filter((n) => findOperator(n.operatorId)?.kind === "source");
  const sinks = state.nodes.filter((n) => findOperator(n.operatorId)?.kind === "sink");
  if (sources.length === 0) issues.push("graph needs exactly one source (it has none)");
  if (sources.length > 1) issues.push("graph must have exactly one source");
  if (sinks.length === 0) issues.push("graph needs at least one sink");
  const indeg = new Map<string, number>();
  for (const e of state.edges) indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
  for (const n of state.nodes) {
    if (findOperator(n.operatorId)?.kind === "source") continue;
    const d = indeg.get(n.id) ?? 0;
    if (d !== 1) {
      const label = findOperator(n.operatorId)?.label ?? n.operatorId;
      issues.push(
        d === 0
          ? `"${label}" isn't connected — every node needs one incoming edge`
          : `"${label}" has ${d} incoming edges (fan-in isn't supported yet)`,
      );
    }
  }
  return issues;
}

export function serializeGraph(
  state: GraphBuilderState,
  meta: { name: string; mode?: "batch" | "stream"; engine?: Engine },
): PipelineConfigJson {
  const nodes = state.nodes.map((n) => {
    const op = findOperator(n.operatorId);
    const kind = op?.kind ?? "transform";
    if (kind === "transform") {
      return {
        id: n.id,
        type: "transform",
        transform: { type: op?.connectorType ?? "unknown", ...n.data },
      };
    }
    // source / sink — connector fields are stored flat on the node data.
    return { id: n.id, type: kind, ...n.data };
  });
  const edges = state.edges.map((e) => {
    const out: { from_node: string; to_node: string; when?: string } = {
      from_node: e.source,
      to_node: e.target,
    };
    const w = e.when?.trim();
    if (w) out.when = w;
    return out;
  });
  return {
    name: meta.name,
    mode: meta.mode ?? "batch",
    ...(meta.engine && meta.engine !== "local" ? { engine: meta.engine } : {}),
    // `graph` is an extra key on PipelineConfigJson (index signature allows it).
    graph: { nodes, edges },
  } as PipelineConfigJson;
}

export function deserializeGraph(
  config: (PipelineConfigJson & { graph: GraphConfigJson }) | null,
  connections: { name: string; type: string }[] = [],
): GraphBuilderState {
  if (!config?.graph) return blankGraph();
  const typeByName = new Map(connections.map((c) => [c.name, c.type]));
  const g = config.graph;

  const nodes: GraphBuilderNode[] = g.nodes.map((n) => {
    let operatorId = "transform:filter";
    let data: Record<string, unknown> = {};
    if (n.type === "transform") {
      const tcfg = (n.transform as { type?: string } & Record<string, unknown>) ?? {};
      const spec = OPERATORS.find(
        (op) => op.kind === "transform" && op.connectorType === tcfg.type,
      );
      operatorId = spec?.id ?? "transform:filter";
      data = stripType({ ...tcfg });
    } else {
      const conn = (n as { connection?: string }).connection;
      const spec = OPERATORS.find(
        (op) => op.kind === n.type && op.connectorType === guessConnectorType(conn, n, typeByName),
      );
      operatorId = spec?.id ?? (n.type === "source" ? "source:postgres" : "sink:postgres");
      data = stripType({ ...n });
      delete (data as Record<string, unknown>).id;
    }
    return { id: n.id, operatorId, data, position: { x: 0, y: 0 } };
  });

  const edges: GraphBuilderEdge[] = g.edges.map((e) => ({
    id: nextId("edge"),
    source: e.from_node,
    target: e.to_node,
    when: e.when ?? undefined,
  }));

  layoutGraph(nodes, edges);
  return { nodes, edges };
}

/** Convert a linear builder pipeline into an equivalent graph (source → … →
 *  sink fan-out), so the user can switch to graph mode and add branches. */
export function linearToGraph(state: BuilderState): GraphBuilderState {
  const sorted = reorderNodes(state.nodes);
  const nodes: GraphBuilderNode[] = sorted
    .filter((n) => findOperator(n.operatorId)?.kind !== "call")
    .map((n) => ({ id: n.id, operatorId: n.operatorId, data: n.data, position: { x: 0, y: 0 } }));
  const spine = nodes.filter((n) => findOperator(n.operatorId)?.kind !== "sink");
  const sinks = nodes.filter((n) => findOperator(n.operatorId)?.kind === "sink");
  const edges: GraphBuilderEdge[] = [];
  for (let i = 0; i < spine.length - 1; i++) {
    edges.push({ id: nextId("edge"), source: spine[i].id, target: spine[i + 1].id });
  }
  const tail = spine[spine.length - 1];
  if (tail) {
    for (const s of sinks) {
      // Carry a sink's routing `when` onto the incoming edge.
      const when = typeof s.data.when === "string" ? s.data.when : undefined;
      edges.push({ id: nextId("edge"), source: tail.id, target: s.id, when });
      delete (s.data as Record<string, unknown>).when;
    }
  }
  layoutGraph(nodes, edges);
  return { nodes, edges };
}

/** Layered left→right layout by BFS depth from the source node. */
function layoutGraph(nodes: GraphBuilderNode[], edges: GraphBuilderEdge[]): void {
  const childrenOf = new Map<string, string[]>();
  const indeg = new Map<string, number>();
  for (const n of nodes) indeg.set(n.id, 0);
  for (const e of edges) {
    childrenOf.set(e.source, [...(childrenOf.get(e.source) ?? []), e.target]);
    indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
  }
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const depth = new Map<string, number>();
  const roots = nodes.filter((n) => (indeg.get(n.id) ?? 0) === 0).map((n) => n.id);
  const queue = [...roots];
  for (const r of roots) depth.set(r, 0);
  while (queue.length) {
    const cur = queue.shift()!;
    const d = depth.get(cur) ?? 0;
    for (const child of childrenOf.get(cur) ?? []) {
      if (!depth.has(child) || depth.get(child)! < d + 1) {
        depth.set(child, d + 1);
        queue.push(child);
      }
    }
  }
  const perCol = new Map<number, number>();
  for (const n of nodes) {
    const col = depth.get(n.id) ?? 0;
    const row = perCol.get(col) ?? 0;
    perCol.set(col, row + 1);
    const node = byId.get(n.id)!;
    node.position = { x: col * 320, y: row * 150 };
  }
}
