/**
 * Relationship-aware auto-layout for the ERD designer (Phase AHI, reworked
 * AHJ for readability).
 *
 * A single global dagre pass turns hub-and-spoke schemas into one long column.
 * Instead we:
 *   1. split the graph into **connected components** (clusters of related
 *      tables) via the relationships,
 *   2. lay out each cluster on its own with dagre (so related tables group
 *      tightly and edges flow consistently),
 *   3. **shelf-pack** the cluster bounding boxes into rows so clusters sit
 *      side by side without overlapping, with isolated tables tidied at the
 *      end.
 *
 * Node heights are estimated from column counts so nothing overlaps.
 */

import dagre from "@dagrejs/dagre";
import type { ErdDesign, DesignTable } from "@/lib/erd-design";

const NODE_WIDTH = 220;
const HEADER_H = 30;
const ROW_H = 21;
const PAD_H = 8;
const CLUSTER_GAP_X = 60;
const CLUSTER_GAP_Y = 60;

export type LayoutDirection = "LR" | "TB";

function nodeHeight(columnCount: number): number {
  return HEADER_H + Math.max(1, columnCount) * ROW_H + PAD_H;
}

/** Undirected connected components over the relationship graph. */
function connectedComponents(design: ErdDesign): DesignTable[][] {
  const idToTable = new Map(design.tables.map((t) => [t.id, t]));
  const adj = new Map<string, Set<string>>();
  for (const t of design.tables) adj.set(t.id, new Set());
  for (const r of design.relations) {
    if (r.from === r.to) continue;
    if (adj.has(r.from) && adj.has(r.to)) {
      adj.get(r.from)!.add(r.to);
      adj.get(r.to)!.add(r.from);
    }
  }
  const seen = new Set<string>();
  const comps: DesignTable[][] = [];
  for (const t of design.tables) {
    if (seen.has(t.id)) continue;
    const stack = [t.id];
    const comp: DesignTable[] = [];
    seen.add(t.id);
    while (stack.length) {
      const id = stack.pop()!;
      const tbl = idToTable.get(id);
      if (tbl) comp.push(tbl);
      for (const nb of adj.get(id) ?? []) {
        if (!seen.has(nb)) {
          seen.add(nb);
          stack.push(nb);
        }
      }
    }
    comps.push(comp);
  }
  return comps;
}

interface Placed {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Lay out one cluster with dagre; return node positions (top-left, local) + bbox. */
function layoutCluster(
  comp: DesignTable[],
  design: ErdDesign,
  dir: LayoutDirection,
): { placed: Placed[]; w: number; h: number } {
  if (comp.length === 1) {
    const c = comp[0];
    const h = nodeHeight(c.columns.length);
    return { placed: [{ id: c.id, x: 0, y: 0, w: NODE_WIDTH, h }], w: NODE_WIDTH, h };
  }
  const ids = new Set(comp.map((t) => t.id));
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: dir, ranksep: 80, nodesep: 30, marginx: 0, marginy: 0 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const t of comp) g.setNode(t.id, { width: NODE_WIDTH, height: nodeHeight(t.columns.length) });
  for (const r of design.relations) {
    if (r.from === r.to) continue;
    if (ids.has(r.from) && ids.has(r.to)) g.setEdge(r.from, r.to);
  }
  dagre.layout(g);
  const placed: Placed[] = [];
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const t of comp) {
    const n = g.node(t.id);
    const x = n.x - n.width / 2;
    const y = n.y - n.height / 2;
    placed.push({ id: t.id, x, y, w: n.width, h: n.height });
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x + n.width);
    maxY = Math.max(maxY, y + n.height);
  }
  // Normalize to (0,0).
  for (const p of placed) {
    p.x -= minX;
    p.y -= minY;
  }
  return { placed, w: maxX - minX, h: maxY - minY };
}

/** Return a copy of ``design`` with tidied, non-overlapping positions. */
export function autoLayout(design: ErdDesign, dir: LayoutDirection = "TB"): ErdDesign {
  if (design.tables.length === 0) return design;

  const comps = connectedComponents(design);
  const laid = comps.map((c) => layoutCluster(c, design, dir));

  // Keep connected clusters first (largest area first), singletons last so the
  // lone tables tuck neatly into a trailing grid instead of splitting clusters.
  const order = laid
    .map((l, i) => ({ l, i, single: comps[i].length === 1, area: l.w * l.h }))
    .sort((a, b) => {
      if (a.single !== b.single) return a.single ? 1 : -1;
      return b.area - a.area;
    });

  // Shelf-pack cluster bounding boxes into rows under a target width that keeps
  // the whole diagram roughly square (and at least as wide as the widest cluster).
  const totalArea = laid.reduce((s, l) => s + (l.w + CLUSTER_GAP_X) * (l.h + CLUSTER_GAP_Y), 0);
  const widest = Math.max(...laid.map((l) => l.w));
  const targetWidth = Math.max(widest, Math.sqrt(totalArea) * 1.3);

  const pos = new Map<string, { x: number; y: number }>();
  let cursorX = 0;
  let cursorY = 0;
  let rowHeight = 0;
  for (const { l } of order) {
    if (cursorX > 0 && cursorX + l.w > targetWidth) {
      cursorX = 0;
      cursorY += rowHeight + CLUSTER_GAP_Y;
      rowHeight = 0;
    }
    for (const p of l.placed) {
      pos.set(p.id, { x: Math.round(cursorX + p.x), y: Math.round(cursorY + p.y) });
    }
    cursorX += l.w + CLUSTER_GAP_X;
    rowHeight = Math.max(rowHeight, l.h);
  }

  const tables = design.tables.map((t) => {
    const p = pos.get(t.id);
    return p ? { ...t, x: p.x, y: p.y } : t;
  });
  return { ...design, tables };
}
