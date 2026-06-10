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

// Generous size estimates (slightly larger than the rendered node) so dagre
// never packs boxes close enough to overlap.
const NODE_WIDTH = 240;
const HEADER_H = 40;
const ROW_H = 25;
const PAD_H = 16;
const CLUSTER_GAP_X = 90;
const CLUSTER_GAP_Y = 90;

export type LayoutDirection = "LR" | "TB";

function nodeHeight(columnCount: number): number {
  return HEADER_H + Math.max(1, columnCount) * ROW_H + PAD_H;
}

/**
 * Remove node overlaps with MINIMAL displacement (Phase AJP), preserving the
 * original arrangement as much as possible. Used after a .damx import so tables
 * keep their DA# positions but no two boxes (or their edges) collide. Iterative
 * pairwise separation along the axis of least penetration, leaving a gap.
 */
export function removeOverlaps(design: ErdDesign, gap = 28): ErdDesign {
  const nodes = design.tables.map((t) => ({
    id: t.id,
    x: t.x,
    y: t.y,
    w: t.w ?? NODE_WIDTH,
    h: nodeHeight(t.columns.length),
  }));
  for (let iter = 0; iter < 80; iter++) {
    let moved = false;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i];
        const b = nodes[j];
        const dx = a.x + a.w / 2 - (b.x + b.w / 2);
        const dy = a.y + a.h / 2 - (b.y + b.h / 2);
        const ovx = (a.w + b.w) / 2 + gap - Math.abs(dx);
        const ovy = (a.h + b.h) / 2 + gap - Math.abs(dy);
        if (ovx > 0 && ovy > 0) {
          moved = true;
          if (ovx < ovy) {
            const push = (ovx / 2) * (dx >= 0 ? 1 : -1);
            a.x += push;
            b.x -= push;
          } else {
            const push = (ovy / 2) * (dy >= 0 ? 1 : -1);
            a.y += push;
            b.y -= push;
          }
        }
      }
    }
    if (!moved) break;
  }
  const byId = new Map(nodes.map((n) => [n.id, n]));
  return {
    ...design,
    tables: design.tables.map((t) => {
      const n = byId.get(t.id)!;
      return { ...t, x: Math.round(n.x), y: Math.round(n.y) };
    }),
  };
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
  // Generous separation so crow's-foot edges have room and overlap less:
  // ranksep between ranks, nodesep within a rank, edgesep between parallel edges.
  g.setGraph({ rankdir: dir, ranksep: 150, nodesep: 70, edgesep: 30, marginx: 0, marginy: 0 });
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

/**
 * Fill per-tab positions for subject areas (Phase AKH). Areas that came with
 * reliable DA# positions get an overlap-separation pass; areas without get a
 * fresh auto-layout of just that tab's tables. Global table x/y is untouched.
 */
export function layoutAreas(design: ErdDesign): ErdDesign {
  if (!design.areas?.length) return design;
  const byId = new Map(design.tables.map((t) => [t.id, t]));
  const areas = design.areas.map((a) => {
    const members = a.tableIds.map((id) => byId.get(id)).filter((t): t is DesignTable => !!t);
    if (members.length === 0) return a;
    const ids = new Set(a.tableIds);
    const sub: ErdDesign = {
      tables: members.map((t) => ({
        ...t,
        x: a.positions?.[t.id]?.x ?? t.x,
        y: a.positions?.[t.id]?.y ?? t.y,
      })),
      relations: design.relations.filter((r) => ids.has(r.from) && ids.has(r.to)),
    };
    const hasPositions = !!a.positions && Object.keys(a.positions).length >= Math.min(2, members.length);
    const laid = hasPositions ? removeOverlaps(sub) : autoLayout(sub);
    const positions: Record<string, { x: number; y: number }> = {};
    for (const t of laid.tables) positions[t.id] = { x: t.x, y: t.y };
    return { ...a, positions };
  });
  return { ...design, areas };
}
