/**
 * Connection usage index — Phase ACL (2026-06-04).
 *
 * The connections list shows *what* connections exist but not *who*
 * uses them. Before deleting a connection an operator/data-engineer
 * has to guess whether a pipeline still references it; an analyst
 * tracing a dataset wants to know which connections feed it. This
 * module walks every pipeline's ``current_config_json`` and counts,
 * per connection NAME, the pipelines that reference it.
 *
 * Connections are referenced by NAME in configs (the runtime resolves
 * the name to a connection at build time), so the index is keyed by
 * name — which is exactly what the connections list renders.
 *
 * All three config shapes are honoured, matching
 * ``migrationSummaryOf``: linear ``source`` / ``sink``, fan-out
 * ``sources`` / ``sinks``, and ``graph.nodes`` (source/sink nodes).
 */

function connectionOf(obj: unknown): string | null {
  if (!obj || typeof obj !== "object") return null;
  const c = (obj as Record<string, unknown>).connection;
  return typeof c === "string" && c.length > 0 ? c : null;
}

/** Every distinct connection NAME a single pipeline config references.
 *  Deduped — a pipeline that reads and writes the same connection
 *  counts once for that pipeline. */
export function extractConnectionNames(
  config: Record<string, unknown> | null | undefined,
): Set<string> {
  const names = new Set<string>();
  if (!config || typeof config !== "object") return names;

  const add = (obj: unknown): void => {
    const name = connectionOf(obj);
    if (name) names.add(name);
  };

  // Linear single source / sink.
  add(config.source);
  add(config.sink);
  // Fan-in / fan-out arrays.
  if (Array.isArray(config.sources)) config.sources.forEach(add);
  if (Array.isArray(config.sinks)) config.sinks.forEach(add);
  // DLQ target (both linear and graph) — a dead-letter sink is a real
  // connection reference; a connection used *only* as a DLQ target
  // would otherwise read as "unused" and be deleted out from under a
  // pipeline. ``serializeGraph`` emits ``config.dlq = { connection }``.
  add(config.dlq);
  // Graph mode — source / sink nodes carry a flat ``connection`` (the
  // builder spreads ``node.data`` onto the wire node). ``sql_exec`` is
  // a source-kind "Run SQL" node emitted with ``type: "sql_exec"``
  // (not "source"), so include it or its connection goes uncounted.
  const graph = config.graph as { nodes?: unknown[] } | undefined;
  if (graph && Array.isArray(graph.nodes)) {
    for (const node of graph.nodes) {
      if (!node || typeof node !== "object") continue;
      const type = (node as Record<string, unknown>).type;
      if (type === "source" || type === "sink" || type === "sql_exec") {
        add(node);
      }
    }
  }
  return names;
}

export interface PipelineRef {
  id: string;
  name: string;
  config: Record<string, unknown> | null;
}

/** Build ``connection name → referencing pipelines``. A pipeline
 *  appears at most once per connection (the per-config set is deduped),
 *  so ``.length`` is a true pipeline count, not a reference count. */
export function buildConnectionUsage(
  pipelines: PipelineRef[],
): Map<string, { id: string; name: string }[]> {
  const usage = new Map<string, { id: string; name: string }[]>();
  for (const p of pipelines) {
    const names = extractConnectionNames(p.config);
    for (const name of names) {
      const bucket = usage.get(name);
      const entry = { id: p.id, name: p.name };
      if (bucket) bucket.push(entry);
      else usage.set(name, [entry]);
    }
  }
  return usage;
}
