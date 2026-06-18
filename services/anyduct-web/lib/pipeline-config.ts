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
  // Pipeline-local variables (ADR-0041). Referenced as ${var.name}; override
  // workspace globals of the same name at build time.
  variables?: Record<string, unknown>;
  // Asset-driven orchestration (ADR-0037). Auto-run when an upstream run
  // materializes one of this pipeline's input assets.
  auto_materialize?: boolean;
  // Freshness SLA in minutes (ADR-0038). Scheduler re-runs this pipeline when
  // its outputs go staler than this. Null/absent = off.
  freshness_sla_minutes?: number | null;
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
  operator: 1,
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
  // Phase AAS follow-up 2 (2026-06-01) — user report "파이프라인
  // 템플릿이 적용이 다 안되는데". Root cause: ``makeNode`` started a
  // node with ``data: {}``; ``defaultValue`` on each field was only
  // consulted by the input renderer as a fallback (``value ??
  // defaultValue``), never copied onto the node. So:
  //
  //   * a template that didn't override every field shipped the
  //     un-defaulted ones as missing on the wire;
  //   * a freshly-dropped node looked filled in the UI but
  //     ``serializeGraph`` emitted bare keys;
  //   * the worker then ran with append/0/empty placeholders instead
  //     of what the user thought they'd configured.
  //
  // Pulling defaults into ``data`` here makes the wire shape match
  // what the user sees. Template ``overrides`` still win because the
  // ``state()`` helper merges them *after* this returns.
  const data: Record<string, unknown> = {};
  for (const f of spec.fields) {
    const dv = (f as { defaultValue?: unknown }).defaultValue;
    if (dv !== undefined) data[f.key] = dv;
  }
  return {
    id: nextId(spec.kind),
    operatorId,
    data,
  };
}

export function reorderNodes(nodes: BuilderNode[]): BuilderNode[] {
  return [...nodes].sort((a, b) => {
    const ka = findOperator(a.operatorId)?.kind ?? "transform";
    const kb = findOperator(b.operatorId)?.kind ?? "transform";
    return KIND_ORDER[ka] - KIND_ORDER[kb];
  });
}

// NOTE: ``serialize()`` (linear-shape config emitter) was removed
// 2026-05-26 as part of the L2 cleanup. Graph-only mode (since the
// May 18 builder overhaul) routes every save through ``serializeGraph``
// — and after L2 dropped the source/sink requirement the dead
// function's old "Pipeline needs a sink operator" throw was the ONLY
// thing in the entire bundle that could still produce that user-visible
// message, which was firing for users on stale Next dev chunks. Removed
// outright so the misleading text can no longer be served from any
// cached chunk on the next rebuild. ``deserialize()`` below stays —
// it's the load-side reader that migrates legacy linear configs into
// the graph editor.

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
 *  (ADR-0030 + ADR-0041 G2/I1). Returns human-readable problems; empty =
 *  valid to save.
 *
 *  Shape rules:
 *  * ≥1 source (multi-source allowed since the materialize engine merges
 *    them via explicit `join` nodes);
 *  * ≥1 sink;
 *  * non-source non-join nodes (transform / sink / aggregate) take exactly
 *    one incoming edge;
 *  * join nodes need ≥2 incoming edges (fan-in is the whole point).
 */
/** Structured shape of one validation finding (Phase L1).
 *  Carries an optional ``nodeId`` so the UI banner can scroll to + select
 *  the offending node when clicked — without an id (e.g. "needs a source")
 *  the issue is global and gets a non-clickable bullet. ``kind`` lets
 *  the renderer pick an icon / colour without parsing the message. */
export type GraphIssueKind =
  | "missing_source"
  | "missing_sink"
  | "missing_input"
  | "wrong_fanin"
  | "join_needs_two"
  | "missing_connection"
  // Orchestration (Operator DAG, ADR-0099)
  | "missing_name"
  | "duplicate_name"
  | "missing_statement";

export interface GraphIssue {
  kind: GraphIssueKind;
  message: string;
  /** ``null`` for graph-wide issues (missing source / sink). */
  nodeId: string | null;
}

export function validateGraph(state: GraphBuilderState): string[] {
  // Backwards-compatible string view — call sites that only need the
  // headline messages keep working. New UI consumers should use
  // :func:`validateGraphStructured` for the clickable banner.
  return validateGraphStructured(state).map((i) => i.message);
}

/** Same rules as :func:`validateGraph` but returns rich objects.
 *
 *  Phase L1 expansion (2026-05-26): the banner needs more than the
 *  first message — it lists all issues, each clickable to focus the
 *  offending node. Adding the structured shape next to the legacy one
 *  keeps yaml_sync / server contracts untouched (they use the string
 *  shape) while letting the new banner render rich rows.
 */
export function validateGraphStructured(state: GraphBuilderState): GraphIssue[] {
  const issues: GraphIssue[] = [];
  // L2 2026-05-26 user request "SOURCE로 시작, SINK로 끝나야한다는
  // 강제성 제거" — drop the "≥1 source / ≥1 sink" rules. A graph that
  // only contains a standalone Run SQL source (side-effect only, no
  // downstream) is now valid; structural per-node rules still apply
  // (transforms/sinks need exactly 1 incoming edge, join needs ≥2)
  // so a dangling sink-with-no-input still surfaces as an issue.
  const indeg = new Map<string, number>();
  for (const e of state.edges) indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
  for (const n of state.nodes) {
    const op = findOperator(n.operatorId);
    if (op?.kind === "source") {
      // Source/sink with no connection — caught the moment the user
      // drops it. Cheaper to flag here than to wait for the dry-run.
      if (!n.data.connection) {
        issues.push({
          kind: "missing_connection",
          message: `"${op?.label ?? n.operatorId}" needs a connection`,
          nodeId: n.id,
        });
      }
      continue;
    }
    if (op?.kind === "sink" && !n.data.connection) {
      issues.push({
        kind: "missing_connection",
        message: `"${op?.label ?? n.operatorId}" needs a connection`,
        nodeId: n.id,
      });
    }
    const d = indeg.get(n.id) ?? 0;
    const label = op?.label ?? n.operatorId;
    if (op?.multiInput) {
      if (d < 2) {
        issues.push({
          kind: "join_needs_two",
          nodeId: n.id,
          message:
            d === 0
              ? `"${label}" isn't connected — join needs at least two incoming edges`
              : `"${label}" only has ${d} incoming edge — join needs at least two`,
        });
      }
      continue;
    }
    if (d !== 1) {
      issues.push({
        kind: d === 0 ? "missing_input" : "wrong_fanin",
        nodeId: n.id,
        message:
          d === 0
            ? `"${label}" isn't connected — every node needs one incoming edge`
            : `"${label}" has ${d} incoming edges — use a join node to merge`,
      });
    }
  }
  return issues;
}

export function serializeGraph(
  state: GraphBuilderState,
  meta: {
    name: string;
    mode?: "batch" | "stream";
    variables?: Record<string, unknown>;
    auto_materialize?: boolean;
    freshness_sla_minutes?: number | null;
    retry?: RetrySettings;
    dlq?: DlqSettings;
  },
): PipelineConfigJson {
  const nodes = state.nodes.map((n) => {
    const op = findOperator(n.operatorId);
    const kind = op?.kind ?? "transform";
    if (kind === "transform") {
      // ADR-0041 I1: join + aggregate are first-class graph node types
      // (not transform wrappers) so the core router can fan-in / group
      // by node.type === "join" / "aggregate". Their fields live flat on
      // the node, matching ``GraphNodeConfig``'s top-level ``on``/``how``
      // / ``group_by``/``aggregations`` columns.
      if (op?.connectorType === "join" || op?.connectorType === "aggregate") {
        return { id: n.id, type: op.connectorType, ...n.data };
      }
      return {
        id: n.id,
        type: "transform",
        transform: { type: op?.connectorType ?? "unknown", ...n.data },
      };
    }
    // source / sink — connector fields are stored flat on the node data.
    // The standalone "Run SQL" operator (ADR-0042 follow-up) is a source
    // kind on the client (for palette + validation reasons) but the
    // server distinguishes it via GraphNodeConfig.type === "sql_exec",
    // so emit that wire type instead of plain "source".
    const wireType = op?.connectorType === "sql_exec" ? "sql_exec" : kind;
    return { id: n.id, type: wireType, ...n.data };
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
  const out: PipelineConfigJson = {
    name: meta.name,
    mode: meta.mode ?? "batch",
    ...(meta.variables && Object.keys(meta.variables).length
      ? { variables: meta.variables }
      : {}),
    ...(meta.auto_materialize ? { auto_materialize: true } : {}),
    ...(meta.freshness_sla_minutes
      ? { freshness_sla_minutes: meta.freshness_sla_minutes }
      : {}),
    // `graph` is an extra key on PipelineConfigJson (index signature allows it).
    graph: { nodes, edges },
  } as PipelineConfigJson;
  // Retry + DLQ pipe through unchanged — graph-mode runs honour the same
  // policy knobs as linear (core PipelineConfig is shape-agnostic).
  if (meta.retry?.enabled) {
    out.retry = {
      max_attempts: meta.retry.max_attempts,
      backoff: meta.retry.backoff,
      initial_delay_seconds: meta.retry.initial_delay_seconds,
    };
  }
  if (meta.dlq?.enabled && meta.dlq.connection) {
    const dlq: PipelineConfigJson["dlq"] = {
      connection: meta.dlq.connection,
      mode: meta.dlq.mode,
    };
    if (meta.dlq.table) dlq.table = meta.dlq.table;
    if (meta.dlq.topic) dlq.topic = meta.dlq.topic;
    out.dlq = dlq;
  }
  return out;
}

/* ───────────────────────────────────────────────────────────────────────────
   Orchestration / Operator DAG (ADR-0099)

   A task-DAG pipeline (`tasks: [...]` + `depends_on`) is edited on the SAME
   canvas as a dataflow graph, but each node is an *operator* (Load / Run SQL /
   Call procedure) and edges are *ordering* (dependency), not data. Nodes carry
   a unique ``name``; depends_on is reconstructed from the incoming edges.
   ─────────────────────────────────────────────────────────────────────────── */

interface TaskJson {
  name: string;
  kind?: string;
  depends_on?: string[];
  trigger_rule?: string;
  timeout_seconds?: number;
  retry?: { max_attempts: number; backoff: string; initial_delay_seconds: number };
  source?: { connection?: string; query?: string; [k: string]: unknown };
  sink?: { connection?: string; [k: string]: unknown } | null;
  sinks?: { connection?: string; [k: string]: unknown }[];
  connection?: string;
  statements?: string[];
  procedure?: string;
  args?: string[];
  reads?: string[];
  writes?: string[];
  [k: string]: unknown;
}

export function isTasksConfig(
  config: PipelineConfigJson | null,
): config is PipelineConfigJson & { tasks: TaskJson[] } {
  const t = (config as { tasks?: unknown } | null)?.tasks;
  return Boolean(config && !isGraphConfig(config) && Array.isArray(t) && t.length > 0);
}

/** One Load (etl) node to start a new orchestration pipeline. */
export function blankOrchestration(): GraphBuilderState {
  const n = makeNode("op:load");
  return {
    nodes: [{ id: n.id, operatorId: n.operatorId, data: { ...n.data }, position: { x: 0, y: 80 } }],
    edges: [],
  };
}

export function serializeTasksDAG(
  state: GraphBuilderState,
  meta: {
    name: string;
    mode?: "batch" | "stream";
    variables?: Record<string, unknown>;
    auto_materialize?: boolean;
    freshness_sla_minutes?: number | null;
    retry?: RetrySettings;
    dlq?: DlqSettings;
  },
): PipelineConfigJson {
  const nameOf = (id: string): string => {
    const n = state.nodes.find((x) => x.id === id);
    return (n?.data.name as string) || id;
  };
  const tasks = state.nodes.map((n) => {
    const op = findOperator(n.operatorId);
    const d = n.data;
    const task: TaskJson = { name: (d.name as string) || n.id };
    const deps = state.edges.filter((e) => e.target === n.id).map((e) => nameOf(e.source));
    if (deps.length) task.depends_on = deps;
    if (d.trigger_rule && d.trigger_rule !== "all_success") {
      task.trigger_rule = d.trigger_rule as string;
    }
    const timeout = Number(d.timeout_seconds);
    if (d.timeout_seconds !== undefined && d.timeout_seconds !== "" && timeout > 0) {
      task.timeout_seconds = timeout;
    }
    const attempts = Number(d.retry_max_attempts);
    if (d.retry_max_attempts !== undefined && d.retry_max_attempts !== "" && attempts > 1) {
      task.retry = { max_attempts: attempts, backoff: "exponential", initial_delay_seconds: 5 };
    }
    if (op?.connectorType === "sql") {
      task.kind = "sql";
      task.connection = d.connection as string;
      // The editor holds one or more statements separated by ';'. Split so the
      // core runs each (committed in order). Round-trips with the ';\n\n' join
      // in deserialize. (Caveat: a ';' inside a string literal would split —
      // rare for DDL/DML; documented.)
      task.statements = String(d.statement ?? "")
        .split(";")
        .map((s) => s.trim())
        .filter(Boolean);
    } else if (op?.connectorType === "proc_call") {
      task.kind = "proc_call";
      task.connection = d.connection as string;
      task.procedure = d.procedure as string;
      task.args = Array.isArray(d.args) ? (d.args as string[]) : [];
      if (Array.isArray(d.reads) && d.reads.length) task.reads = d.reads as string[];
      if (Array.isArray(d.writes) && d.writes.length) task.writes = d.writes as string[];
    } else {
      // etl "Load": source read connection, sink write connection (defaults to
      // the same — in-database INSERT…SELECT; different = cross-DB load).
      const conn = d.connection as string;
      const sinkConn = (d.sink_connection as string) || conn;
      task.source = { connection: conn, ...(d.query ? { query: d.query as string } : {}) };
      const sink: Record<string, unknown> = {
        connection: sinkConn,
        mode: (d.mode as string) ?? "append",
      };
      if (d.table) sink.table = d.table;
      if (d.pre_sql) sink.pre_sql = d.pre_sql;
      const kc = (d.key_columns as string | undefined)?.trim();
      if (kc) sink.key_columns = kc.split(",").map((s) => s.trim()).filter(Boolean);
      task.sink = sink;
    }
    return task;
  });
  const out: PipelineConfigJson = {
    name: meta.name,
    mode: meta.mode ?? "batch",
    ...(meta.variables && Object.keys(meta.variables).length ? { variables: meta.variables } : {}),
    ...(meta.auto_materialize ? { auto_materialize: true } : {}),
    ...(meta.freshness_sla_minutes ? { freshness_sla_minutes: meta.freshness_sla_minutes } : {}),
    tasks,
  } as PipelineConfigJson;
  if (meta.retry?.enabled) {
    out.retry = {
      max_attempts: meta.retry.max_attempts,
      backoff: meta.retry.backoff,
      initial_delay_seconds: meta.retry.initial_delay_seconds,
    };
  }
  if (meta.dlq?.enabled && meta.dlq.connection) {
    const dlq: PipelineConfigJson["dlq"] = { connection: meta.dlq.connection, mode: meta.dlq.mode };
    if (meta.dlq.table) dlq.table = meta.dlq.table;
    if (meta.dlq.topic) dlq.topic = meta.dlq.topic;
    out.dlq = dlq;
  }
  return out;
}

export function deserializeTasksDAG(config: PipelineConfigJson | null): GraphBuilderState {
  const tasks = ((config as { tasks?: TaskJson[] } | null)?.tasks ?? []) as TaskJson[];
  const nodes: GraphBuilderNode[] = [];
  const nameToId = new Map<string, string>();
  for (const t of tasks) {
    let operatorId = "op:load";
    let data: Record<string, unknown> = { name: t.name };
    if (t.kind === "sql") {
      operatorId = "op:sql";
      // Join the statement list back into one editable SQL block (split by ';'
      // on save). Preserves multi-statement sql steps through a round-trip.
      data = { name: t.name, connection: t.connection, statement: (t.statements ?? []).join(";\n\n") };
    } else if (t.kind === "proc_call") {
      operatorId = "op:proc_call";
      data = {
        name: t.name,
        connection: t.connection,
        procedure: t.procedure,
        args: t.args ?? [],
        reads: t.reads ?? [],
        writes: t.writes ?? [],
      };
    } else {
      const src = t.source ?? {};
      const snk = t.sink ?? t.sinks?.[0] ?? {};
      const kc = snk.key_columns;
      const srcConn = src.connection ?? snk.connection;
      data = {
        name: t.name,
        connection: srcConn,
        // Only surface a write connection when it actually differs (keeps the
        // common same-connection case clean).
        sink_connection: snk.connection && snk.connection !== srcConn ? snk.connection : undefined,
        query: src.query,
        table: snk.table,
        mode: snk.mode ?? "append",
        pre_sql: snk.pre_sql,
        key_columns: Array.isArray(kc) ? kc.join(",") : kc,
      };
    }
    if (t.trigger_rule) data.trigger_rule = t.trigger_rule;
    if (t.timeout_seconds !== undefined && t.timeout_seconds !== null) {
      data.timeout_seconds = t.timeout_seconds;
    }
    if (t.retry?.max_attempts) data.retry_max_attempts = t.retry.max_attempts;
    const id = nextId("op");
    nameToId.set(t.name, id);
    nodes.push({ id, operatorId, data, position: { x: 0, y: 0 } });
  }
  const edges: GraphBuilderEdge[] = [];
  for (const t of tasks) {
    const tid = nameToId.get(t.name);
    if (!tid) continue;
    for (const dep of t.depends_on ?? []) {
      const sid = nameToId.get(dep);
      if (sid) edges.push({ id: nextId("edge"), source: sid, target: tid });
    }
  }
  layoutGraph(nodes, edges);
  return { nodes, edges };
}

/** Client-side validation for an Operator DAG: unique non-empty names +
 *  per-operator required fields. (Acyclicity is enforced by the canvas.) */
export function validateTasksDAG(state: GraphBuilderState): GraphIssue[] {
  const issues: GraphIssue[] = [];
  const seen = new Map<string, number>();
  for (const n of state.nodes) {
    const op = findOperator(n.operatorId);
    const name = (n.data.name as string | undefined)?.trim();
    const label = op ? op.label : "Step";
    if (!name) {
      issues.push({ kind: "missing_name", message: `A "${label}" step needs a name`, nodeId: n.id });
    } else {
      seen.set(name, (seen.get(name) ?? 0) + 1);
    }
    if (!n.data.connection) {
      issues.push({ kind: "missing_connection", message: `"${name ?? label}" needs a connection`, nodeId: n.id });
    }
    if (op?.connectorType === "sql" && !(n.data.statement as string | undefined)?.trim()) {
      issues.push({ kind: "missing_statement", message: `"${name ?? label}" needs a SQL statement`, nodeId: n.id });
    }
    if (op?.connectorType === "proc_call" && !(n.data.procedure as string | undefined)?.trim()) {
      issues.push({ kind: "missing_statement", message: `"${name ?? label}" needs a procedure`, nodeId: n.id });
    }
  }
  for (const [name, count] of seen) {
    if (count > 1) {
      const dupes = state.nodes.filter((n) => (n.data.name as string)?.trim() === name);
      for (const n of dupes) {
        issues.push({ kind: "duplicate_name", message: `Duplicate step name "${name}"`, nodeId: n.id });
      }
    }
  }
  return issues;
}

/** Pull policy / behaviour metadata off a stored ``PipelineConfigJson`` so
 *  graph-mode state can carry them separately from the dataflow graph
 *  (ADR-0030 + graph-only mode, 2026-05-26). Sensible defaults so a
 *  pipeline saved before retry/dlq existed loads cleanly. */
export function extractPipelineMeta(
  config: PipelineConfigJson | null,
): {
  variables: Record<string, unknown>;
  auto_materialize: boolean;
  freshness_sla_minutes: number | null;
  retry: RetrySettings;
  dlq: DlqSettings;
} {
  return {
    variables:
      config?.variables && typeof config.variables === "object" ? config.variables : {},
    auto_materialize: Boolean(config?.auto_materialize),
    freshness_sla_minutes:
      typeof config?.freshness_sla_minutes === "number"
        ? config.freshness_sla_minutes
        : null,
    retry: config?.retry
      ? {
          enabled: true,
          max_attempts: config.retry.max_attempts ?? DEFAULT_RETRY.max_attempts,
          backoff: (config.retry.backoff ?? DEFAULT_RETRY.backoff) as RetrySettings["backoff"],
          initial_delay_seconds:
            config.retry.initial_delay_seconds ?? DEFAULT_RETRY.initial_delay_seconds,
        }
      : { ...DEFAULT_RETRY },
    dlq: config?.dlq
      ? {
          enabled: true,
          connection: config.dlq.connection ?? "",
          mode: (config.dlq.mode ?? DEFAULT_DLQ.mode) as DlqSettings["mode"],
          table: config.dlq.table ?? "",
          topic: config.dlq.topic ?? "",
        }
      : { ...DEFAULT_DLQ },
  };
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
    } else if (n.type === "join" || n.type === "aggregate") {
      // ADR-0041 I1: first-class graph node types with their fields stored
      // flat on the node. Lift them back into the matching operator + data.
      const spec = OPERATORS.find(
        (op) => op.kind === "transform" && op.connectorType === n.type,
      );
      operatorId = spec?.id ?? "transform:filter";
      data = stripType({ ...n });
      delete (data as Record<string, unknown>).id;
    } else if (n.type === "sql_exec") {
      // ADR-0042 follow-up — standalone Run SQL node. Re-attach to the
      // source-kind catalogue entry whose connectorType matches.
      operatorId = "source:sql_exec";
      data = stripType({ ...n });
      delete (data as Record<string, unknown>).id;
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
  const nodes: GraphBuilderNode[] = sorted.map((n) => ({
    id: n.id,
    operatorId: n.operatorId,
    data: n.data,
    position: { x: 0, y: 0 },
  }));
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
