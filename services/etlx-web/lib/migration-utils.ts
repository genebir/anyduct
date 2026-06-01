/**
 * Cross-DB migration detector + summary — Phase AAN (2026-05-29).
 *
 * The "Migrations" menu in the sidebar is a thin filter over the
 * existing pipelines list: it shows pipelines whose at-least-one sink
 * has ``auto_create_table: true`` (i.e. the runtime is on the hook
 * for creating the destination table from the source schema —
 * ADR-0066 / 0071 / 0072).
 *
 * Splitting the detector out of the page lets us:
 *  - keep the page presentational,
 *  - reuse the same predicate from any future surface that filters
 *    pipelines by intent (dashboard, search, etc.),
 *  - and unit-test the predicate without rendering React.
 *
 * The function takes the raw ``current_config_json`` shape the
 * server returns; we don't try to type the whole config tree
 * here — the runtime contract is what matters, not the in-memory
 * BuilderState.
 */

export interface MigrationSummary {
  /** Where the auto-created destination lives — connection name + table. */
  sinkConnection: string | null;
  sinkTable: string | null;
  /** Append / overwrite / upsert. */
  sinkMode: string | null;
  /** skip / drop / error — visible to the operator on the migration row. */
  ifExists: "skip" | "drop" | "error";
  /** Convenience flag — false when ``current_config_json`` was null. */
  hasAnyAutoCreate: boolean;
}

type Sinkish = Record<string, unknown>;

function isAutoCreateSink(sink: unknown): sink is Sinkish {
  if (!sink || typeof sink !== "object") return false;
  return (sink as Sinkish).auto_create_table === true;
}

function pickIfExists(sink: Sinkish): "skip" | "drop" | "error" {
  const v = sink.auto_create_if_exists;
  if (v === "drop" || v === "error") return v;
  return "skip";
}

/**
 * Inspect a pipeline's ``current_config_json`` and return the
 * migration summary if at least one sink has ``auto_create_table:
 * true``. Returns ``null`` for non-migration pipelines so callers can
 * filter with ``.filter(Boolean)``.
 *
 * Honours all three sink shapes: linear ``sink`` (single), linear
 * ``sinks`` (fan-out), and ``graph.nodes`` (graph mode).
 */
export function migrationSummaryOf(
  config: Record<string, unknown> | null | undefined,
): MigrationSummary | null {
  if (!config || typeof config !== "object") return null;

  const sinks: Sinkish[] = [];
  // Linear single sink.
  if (isAutoCreateSink(config.sink)) sinks.push(config.sink as Sinkish);
  // Linear fan-out.
  if (Array.isArray(config.sinks)) {
    for (const s of config.sinks) if (isAutoCreateSink(s)) sinks.push(s);
  }
  // Graph mode.
  const graph = config.graph as { nodes?: unknown[] } | undefined;
  if (graph && Array.isArray(graph.nodes)) {
    for (const node of graph.nodes) {
      if (
        node &&
        typeof node === "object" &&
        (node as Record<string, unknown>).type === "sink" &&
        isAutoCreateSink(node)
      ) {
        sinks.push(node as Sinkish);
      }
    }
  }
  if (sinks.length === 0) return null;

  // Surface the *first* matching sink so the row stays one-line.
  // Multi-sink fan-out is uncommon in migration patterns; if it
  // happens, a future slice can paint a per-sink breakdown.
  const first = sinks[0];
  return {
    sinkConnection:
      typeof first.connection === "string" ? first.connection : null,
    sinkTable: typeof first.table === "string" ? first.table : null,
    sinkMode: typeof first.mode === "string" ? first.mode : null,
    ifExists: pickIfExists(first),
    hasAnyAutoCreate: true,
  };
}
