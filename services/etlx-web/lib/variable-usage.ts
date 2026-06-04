/**
 * Workspace-variable usage index — Phase ACR (2026-06-04).
 *
 * Sibling of ``connection-usage`` (ACL). The variables page lists what
 * variables exist but not who references them, so deleting one is a
 * guess: a pipeline that interpolates ``${var.warehouse}`` will fail
 * its next run if the variable vanishes. This module scans every
 * pipeline's ``current_config_json`` for ``${var.<name>}`` tokens and
 * counts, per variable name, the pipelines that reference it.
 *
 * Variables can appear anywhere a string lives in the config (source
 * query, sink table, transform args, graph nodes, …), so rather than
 * walk the shape we stringify the whole config and match the token —
 * the same ``${var.name}`` syntax the core resolver uses
 * (``etl_plugins/config/variables.py``: name = ``[a-zA-Z_][a-zA-Z0-9_]*``).
 *
 * Edge: a pipeline that shadows a workspace variable with a same-named
 * pipeline-local one (``config.variables``) still counts here. That
 * over-counts slightly, but for a *delete-safety* signal erring toward
 * "still referenced" is the safe direction.
 */

const VAR_REF = /\$\{var\.([a-zA-Z_][a-zA-Z0-9_]*)\}/g;

/** Every distinct variable name a single pipeline config references. */
export function referencedVariableNames(
  config: Record<string, unknown> | null | undefined,
): Set<string> {
  const names = new Set<string>();
  if (!config || typeof config !== "object") return names;
  // Stringify once; the token can be nested arbitrarily deep so a flat
  // text scan is both simpler and more complete than a shape walk.
  const text = JSON.stringify(config);
  for (const m of text.matchAll(VAR_REF)) {
    names.add(m[1]);
  }
  return names;
}

export interface PipelineRef {
  id: string;
  name: string;
  config: Record<string, unknown> | null;
}

/** Build ``variable name → referencing pipelines``. A pipeline appears
 *  at most once per variable (the per-config set is deduped). */
export function buildVariableUsage(
  pipelines: PipelineRef[],
): Map<string, { id: string; name: string }[]> {
  const usage = new Map<string, { id: string; name: string }[]>();
  for (const p of pipelines) {
    for (const name of referencedVariableNames(p.config)) {
      const bucket = usage.get(name);
      const entry = { id: p.id, name: p.name };
      if (bucket) bucket.push(entry);
      else usage.set(name, [entry]);
    }
  }
  return usage;
}
