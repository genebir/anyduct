/**
 * Relationship-aware auto-layout for the ERD designer (Phase AHI).
 *
 * Uses dagre (layered DAG layout) to arrange tables so related tables sit
 * near each other and edges flow consistently, instead of a naive grid. Node
 * heights are estimated from column counts so dagre reserves the right space.
 */

import dagre from "@dagrejs/dagre";
import type { ErdDesign } from "@/lib/erd-design";

const NODE_WIDTH = 220;
const HEADER_H = 32;
const ROW_H = 22;
const PAD_H = 10;

export type LayoutDirection = "LR" | "TB";

function estimateHeight(columnCount: number): number {
  return HEADER_H + Math.max(1, columnCount) * ROW_H + PAD_H;
}

/** Return a copy of ``design`` with tidied table positions. */
export function autoLayout(design: ErdDesign, dir: LayoutDirection = "LR"): ErdDesign {
  if (design.tables.length === 0) return design;

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: dir, ranksep: 90, nodesep: 36, marginx: 24, marginy: 24 });
  g.setDefaultEdgeLabel(() => ({}));

  for (const t of design.tables) {
    g.setNode(t.id, { width: NODE_WIDTH, height: estimateHeight(t.columns.length) });
  }
  const tableIds = new Set(design.tables.map((t) => t.id));
  for (const r of design.relations) {
    // Skip self-references (dagre dislikes self-loops) and dangling edges.
    if (r.from === r.to) continue;
    if (tableIds.has(r.from) && tableIds.has(r.to)) g.setEdge(r.from, r.to);
  }

  dagre.layout(g);

  const tables = design.tables.map((t) => {
    const n = g.node(t.id);
    if (!n) return t;
    // dagre gives center coords; xyflow wants top-left.
    return { ...t, x: Math.round(n.x - n.width / 2), y: Math.round(n.y - n.height / 2) };
  });
  return { ...design, tables };
}
