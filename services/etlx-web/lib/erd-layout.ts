/**
 * Visibility-optimized auto-layout for the ERD designer (Phase ALB; history:
 * AHI cluster layout, AHJ shelf packing, AKV visibility sizing).
 *
 * The layout and the edge renderer used to be strangers: dagre placed node
 * *centers* while the crow's-foot edges attach at facing-side anchors with
 * sibling distribution (lib/erd-edge-geometry). The result was a forest of
 * tiny S-bends and overlapping middle segments no spacing constant could fix.
 * This engine instead **predicts the exact lines the renderer will draw** and
 * optimizes node sizes, positions, attachment points and bend channels
 * together:
 *
 *   1. split into connected components (clusters of related tables),
 *   2. dagre pass 1 per cluster → discover which side of each node its edges
 *      actually attach to, then size hubs so the distributed anchors sit
 *      ≥ EDGE_GAP apart and widen the rank gap for crowded edge channels,
 *   3. dagre pass 2 with the final sizes,
 *   4. **anchor-alignment sweeps**: nudge nodes along the cross axis so
 *      connected anchor pairs line up (the renderer snaps ≤ SNAP px deltas to
 *      dead-straight lines) — overlaps resolved by isotonic regression (PAVA)
 *      preserving dagre's crossing-minimized order,
 *   5. **channel assignment**: edges that still need a bend get distinct
 *      middle-segment positions (persisted as relation.centerRatio) chosen to
 *      cross zero tables and keep ≥ CHANNEL_GAP from sibling segments,
 *   6. shelf-pack the cluster bounding boxes (isolated tables tidied last).
 *
 * Everything runs in *rendered* pixels (fontScale-aware); TB direction is the
 * same pipeline on transposed rects.
 */

import dagre from "@dagrejs/dagre";
import type { DesignRelation, DesignTable, ErdDesign } from "@/lib/erd-design";
import {
  bestCenter,
  borderIntersection,
  distributeAnchor,
  hCross,
  pathCrossings,
  rectSide,
  straightSnap,
  vCross,
  type Anchor,
  type GEdge,
  type GPoint,
  type GRect,
} from "@/lib/erd-edge-geometry";

export type LayoutDirection = "LR" | "TB";

const DEFAULT_W = 240;
const CLUSTER_GAP_X = 90;
const CLUSTER_GAP_Y = 90;

/* ── Exact rendered node metrics ────────────────────────────────────────────
   The table node pins its line-height (erd-designer nodeLabel) so the auto
   height is a closed formula. If the node CSS changes, update BOTH places —
   anchor prediction (and therefore straight-line snapping) depends on it. */

/** Rendered height of a table node with auto height: 1px borders (2) +
 *  header (py-1.5 = 12, border-b-2 = 2, one line) + rows (py-1 = 8, one line,
 *  1px divider except after the last row). */
export function contentHeight(columnCount: number, fontScale = 1): number {
  const lh = Math.round(16 * fontScale);
  return 15 + lh + Math.max(0, columnCount) * (lh + 9);
}

/* ── Visibility-driven node sizing (Phase AKV) ────────────────────────────── */

const MIN_W = 200;
const MAX_W = 440;
const EDGE_GAP = 30; // minimum px between edge anchors on a node side

/** Rough rendered text width: mono ~6.7px/char at 11px, Hangul ~11.5px. */
function textW(s: string): number {
  let w = 0;
  for (const ch of s) w += /[가-힣]/.test(ch) ? 11.5 : 6.7;
  return w;
}

/** Width that fits the table header and every column row (icon + name +
 *  gap + type + paddings, mirroring the node renderer's layout). */
export function fitWidth(t: DesignTable): number {
  let w = 40 + textW(t.name);
  for (const c of t.columns) {
    w = Math.max(w, 16 + 6 + textW(c.name) + 14 + textW(c.type ?? "") * 0.91 + 30);
  }
  return Math.round(Math.min(Math.max(w, MIN_W), MAX_W));
}

/**
 * Remove node overlaps with MINIMAL displacement (Phase AJP), preserving the
 * original arrangement as much as possible. Used after a .damx import so tables
 * keep their DA# positions but no two boxes (or their edges) collide. Iterative
 * pairwise separation along the axis of least penetration, leaving a gap.
 */
export function removeOverlaps(design: ErdDesign, gap = 28): ErdDesign {
  const scale = design.fontScale ?? 1;
  const nodes = design.tables.map((t) => ({
    id: t.id,
    x: t.x,
    y: t.y,
    w: Math.round((t.w ?? DEFAULT_W) * scale),
    h: t.h ? Math.round(t.h * scale) : contentHeight(t.columns.length, scale),
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

/* ── Internal layout model (LR space; TB is transposed in/out) ───────────── */

interface LNode {
  id: string;
  /** rect in layout space (rendered px) — mutated through the pipeline */
  r: GRect;
  cols: number;
  degree: number;
}

interface Cluster {
  nodes: LNode[];
  edges: GEdge[];
  w: number;
  h: number;
}

const NODESEP = 56; // dagre separation within a rank
const RANK_GAP_MIN = 110; // minimum gap between ranks (room for the markers + label)
const RANK_GAP_MAX = 320;
const CROSS_GAP = 44; // min cross-axis gap enforced by the alignment sweeps
const CHANNEL_GAP = 16; // min distance between parallel middle segments
const SWEEPS = 18;

/** Undirected connected components over the relationship graph. */
function components(nodes: LNode[], edges: GEdge[]): { nodes: LNode[]; edges: GEdge[] }[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const adj = new Map<string, Set<string>>();
  for (const n of nodes) adj.set(n.id, new Set());
  for (const e of edges) {
    if (e.source === e.target) continue;
    if (adj.has(e.source) && adj.has(e.target)) {
      adj.get(e.source)!.add(e.target);
      adj.get(e.target)!.add(e.source);
    }
  }
  const seen = new Set<string>();
  const out: { nodes: LNode[]; edges: GEdge[] }[] = [];
  for (const n of nodes) {
    if (seen.has(n.id)) continue;
    const stack = [n.id];
    seen.add(n.id);
    const ids = new Set<string>([n.id]);
    while (stack.length) {
      const id = stack.pop()!;
      for (const nb of adj.get(id) ?? []) {
        if (!seen.has(nb)) {
          seen.add(nb);
          ids.add(nb);
          stack.push(nb);
        }
      }
    }
    out.push({
      nodes: [...ids].map((id) => byId.get(id)!),
      edges: edges.filter((e) => ids.has(e.source) && ids.has(e.target) && e.source !== e.target),
    });
  }
  return out;
}

/** One dagre pass (LR) over a component; positions written into node rects. */
function dagrePass(comp: { nodes: LNode[]; edges: GEdge[] }, ranksep: number): void {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", ranksep, nodesep: NODESEP, edgesep: 24, marginx: 0, marginy: 0 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of comp.nodes) g.setNode(n.id, { width: n.r.w, height: n.r.h });
  for (const e of comp.edges) g.setEdge(e.source, e.target);
  dagre.layout(g);
  for (const n of comp.nodes) {
    const d = g.node(n.id);
    n.r.x = d.x - d.width / 2;
    n.r.y = d.y - d.height / 2;
  }
}

/** Group component nodes into ranks by their (shared) center-x after dagre. */
function ranksOf(nodes: LNode[]): LNode[][] {
  const sorted = [...nodes].sort((a, b) => a.r.x + a.r.w / 2 - (b.r.x + b.r.w / 2));
  const ranks: LNode[][] = [];
  let cx = -Infinity;
  for (const n of sorted) {
    const c = n.r.x + n.r.w / 2;
    if (c - cx > 2) {
      ranks.push([n]);
      cx = c;
    } else {
      ranks[ranks.length - 1].push(n);
    }
  }
  for (const r of ranks) r.sort((a, b) => a.r.y - b.r.y);
  return ranks;
}

/** How forgiving the "this edge counts as straight" test is during layout —
 *  slightly under the renderer's SNAP so rounding can't push us over. */
const ALIGN_EPS = 12;

/** Weighted isotonic regression (PAVA): non-decreasing fit of ``desired``. */
function pava(desired: number[], weights: number[]): number[] {
  const level: number[] = [];
  const wsum: number[] = [];
  const count: number[] = [];
  for (let i = 0; i < desired.length; i++) {
    let v = desired[i];
    let w = weights[i];
    let c = 1;
    while (level.length && level[level.length - 1] > v + 1e-9) {
      const lv = level.pop()!;
      const lw = wsum.pop()!;
      const lc = count.pop()!;
      v = (v * w + lv * lw) / (w + lw);
      w += lw;
      c += lc;
    }
    level.push(v);
    wsum.push(w);
    count.push(c);
  }
  const out: number[] = [];
  for (let k = 0; k < level.length; k++) for (let j = 0; j < count[k]; j++) out.push(level[k]);
  return out;
}

/**
 * Best cross-axis shift for one node given its anchor deltas: rather than
 * averaging (which bends EVERY edge a little), commit to the candidate shift
 * that straightens the most edges — degree-2 chain nodes pick one partner to
 * line up with instead of splitting the difference and bending both.
 */
function bestShift(deltas: number[]): number {
  if (deltas.length === 0) return 0;
  if (deltas.length === 1) return deltas[0];
  let best = 0;
  let bestScore = Infinity;
  for (const cand of [0, ...deltas]) {
    let score = 0;
    for (const d of deltas) {
      const off = Math.abs(d - cand);
      score += off <= ALIGN_EPS ? 0 : Math.min(off, 100);
    }
    score += Math.abs(cand) * 0.01; // prefer staying put on ties
    if (score < bestScore - 1e-9) {
      bestScore = score;
      best = cand;
    }
  }
  return best;
}

/**
 * Anchor-alignment sweeps: move nodes along the cross axis (y in LR space) so
 * connected anchor pairs line up. Anchors are recomputed live with the SAME
 * function the renderer uses, so "aligned here" = "dead-straight on canvas"
 * (within the renderer's SNAP tolerance). In-rank order and minimum gaps are
 * preserved via PAVA, so dagre's crossing minimization survives. Gauss-Seidel
 * iteration with a ramping step size; stops when the layout settles.
 */
function alignSweeps(comp: { nodes: LNode[]; edges: GEdge[] }): void {
  if (comp.nodes.length <= 1 || comp.edges.length === 0) return;
  const rects = new Map(comp.nodes.map((n) => [n.id, n.r]));
  const getRect = (id: string) => rects.get(id);
  const incident = new Map<string, GEdge[]>();
  for (const e of comp.edges) {
    if (e.source === e.target) continue;
    (incident.get(e.source) ?? incident.set(e.source, []).get(e.source)!).push(e);
    (incident.get(e.target) ?? incident.set(e.target, []).get(e.target)!).push(e);
  }
  const ranks = ranksOf(comp.nodes);

  const sweepRank = (rank: LNode[], damp: number): number => {
    // Desired cross position per node: shift by the anchor delta of its
    // horizontal edges (anchor-to-anchor, exactly as the renderer attaches).
    const desired: number[] = [];
    const weights: number[] = [];
    for (const n of rank) {
      const deltas: number[] = [];
      for (const e of incident.get(n.id) ?? []) {
        const otherId = e.source === n.id ? e.target : e.source;
        const own = distributeAnchor(n.id, otherId, e.id, comp.edges, getRect);
        const part = distributeAnchor(otherId, n.id, e.id, comp.edges, getRect);
        const horiz =
          (own.side === "left" || own.side === "right") &&
          (part.side === "left" || part.side === "right");
        if (horiz) deltas.push(part.y - own.y);
      }
      desired.push(n.r.y + bestShift(deltas) * damp);
      weights.push(deltas.length ? 1 + deltas.length : 0.25);
    }
    // Project onto "same order, ≥ CROSS_GAP apart" — weighted PAVA on the
    // gap-compensated coordinates.
    const prefix: number[] = [];
    let acc = 0;
    for (const n of rank) {
      prefix.push(acc);
      acc += n.r.h + CROSS_GAP;
    }
    const fitted = pava(
      desired.map((d, i) => d - prefix[i]),
      weights,
    );
    let moved = 0;
    rank.forEach((n, i) => {
      const y = fitted[i] + prefix[i];
      moved = Math.max(moved, Math.abs(y - n.r.y));
      n.r.y = y;
    });
    return moved;
  };

  for (let it = 0; it < SWEEPS; it++) {
    // Soft start (large reshuffles settle), exact finish (kill residuals that
    // would land just over the renderer's snap tolerance).
    const damp = it < 2 ? 0.6 : 1;
    let moved = 0;
    for (const rank of ranks) moved = Math.max(moved, sweepRank(rank, damp)); // left → right
    for (let i = ranks.length - 1; i >= 0; i--) moved = Math.max(moved, sweepRank(ranks[i], damp)); // right → left
    if (it >= 2 && moved < 0.75) break;
  }
}

/**
 * Straightness rescue (greedy hill climb). The sweeps place everyone via
 * rank-wide least-squares, where hub fans — whose leaves can never ALL reach
 * the hub's distributed anchors — drag achievable 1:1 alignments just past
 * the snap tolerance. This pass revisits each node, tries the exact shift
 * that would straighten its best edge, and keeps the move only when the
 * component's total straight-edge count actually goes up (evaluated
 * incrementally over the affected edges, reverted otherwise).
 */
function rescuePass(comp: { nodes: LNode[]; edges: GEdge[] }): void {
  if (comp.nodes.length <= 1 || comp.edges.length === 0) return;
  const rects = new Map(comp.nodes.map((n) => [n.id, n.r]));
  const getRect = (id: string) => rects.get(id);
  const incident = new Map<string, GEdge[]>();
  for (const e of comp.edges) {
    if (e.source === e.target) continue;
    (incident.get(e.source) ?? incident.set(e.source, []).get(e.source)!).push(e);
    (incident.get(e.target) ?? incident.set(e.target, []).get(e.target)!).push(e);
  }
  const ranks = ranksOf(comp.nodes);
  const rankPos = new Map<string, { rank: LNode[]; i: number }>();
  for (const rank of ranks) rank.forEach((n, i) => rankPos.set(n.id, { rank, i }));

  /** Anchor delta of ``e`` seen from ``nodeId`` (positive = partner sits
   *  lower): the exact shift that would align this edge. */
  const horizDeltaFor = (e: GEdge, nodeId: string): number | null => {
    const otherId = e.source === nodeId ? e.target : e.source;
    const own = distributeAnchor(nodeId, otherId, e.id, comp.edges, getRect);
    const part = distributeAnchor(otherId, nodeId, e.id, comp.edges, getRect);
    const horiz =
      (own.side === "left" || own.side === "right") && (part.side === "left" || part.side === "right");
    return horiz ? part.y - own.y : null;
  };
  const isStraight = (e: GEdge): boolean => {
    const d = horizDeltaFor(e, e.source);
    return d !== null && Math.abs(d) <= ALIGN_EPS;
  };
  /** Edges whose anchors can change when ``n`` moves: its own edges plus
   *  everything incident to its partners (sibling redistribution). */
  const affectedEdges = (n: LNode): GEdge[] => {
    const seen = new Map<string, GEdge>();
    for (const e of incident.get(n.id) ?? []) {
      seen.set(e.id, e);
      const partner = e.source === n.id ? e.target : e.source;
      for (const pe of incident.get(partner) ?? []) seen.set(pe.id, pe);
    }
    return [...seen.values()];
  };

  const MIN_GAP = 24; // rescue may tighten the rank a little for a win
  const order = [...comp.nodes].sort(
    (a, b) => (incident.get(a.id)?.length ?? 0) - (incident.get(b.id)?.length ?? 0),
  );
  for (let pass = 0; pass < 2; pass++) {
    for (const n of order) {
      const deltas: number[] = [];
      for (const e of incident.get(n.id) ?? []) {
        const own = distributeAnchor(
          n.id,
          e.source === n.id ? e.target : e.source,
          e.id,
          comp.edges,
          getRect,
        );
        const part = distributeAnchor(
          e.source === n.id ? e.target : e.source,
          n.id,
          e.id,
          comp.edges,
          getRect,
        );
        if (
          (own.side === "left" || own.side === "right") &&
          (part.side === "left" || part.side === "right")
        ) {
          deltas.push(part.y - own.y);
        }
      }
      // Try straightening the closest not-yet-straight edge exactly.
      const targets = deltas
        .filter((d) => Math.abs(d) > ALIGN_EPS && Math.abs(d) <= 200)
        .sort((a, b) => Math.abs(a) - Math.abs(b));
      if (!targets.length) continue;
      const targetEdge = (incident.get(n.id) ?? []).find((e) => {
        const d = horizDeltaFor(e, n.id);
        return d !== null && Math.abs(d - targets[0]) < 0.5;
      });
      if (!targetEdge) continue;
      const rp = rankPos.get(n.id)!;
      const prev = rp.i > 0 ? rp.rank[rp.i - 1] : null;
      const next = rp.i < rp.rank.length - 1 ? rp.rank[rp.i + 1] : null;
      const feasible = (y: number): boolean =>
        (!prev || y >= prev.r.y + prev.r.h + MIN_GAP) &&
        (!next || y + n.r.h <= next.r.y - MIN_GAP);
      const affected = affectedEdges(n);
      const before = affected.filter(isStraight).length;
      const oldY = n.r.y;
      // Intersection-style anchors travel WITH the node, so a single step
      // undershoots — iterate the shift until the target edge actually snaps.
      let ok = false;
      for (let step = 0; step < 5; step++) {
        const d = horizDeltaFor(targetEdge, n.id);
        if (d === null) break;
        if (Math.abs(d) <= ALIGN_EPS) {
          // A straight line THROUGH another table is worse than a bend —
          // only count this as a win when the segment is clean.
          const otherId = targetEdge.source === n.id ? targetEdge.target : targetEdge.source;
          const own = distributeAnchor(n.id, otherId, targetEdge.id, comp.edges, getRect);
          const part = distributeAnchor(otherId, n.id, targetEdge.id, comp.edges, getRect);
          const y = (own.y + part.y) / 2;
          const obstacles = comp.nodes
            .filter((o) => o.id !== n.id && o.id !== otherId)
            .map((o) => o.r);
          ok = segBoxCrossings({ x: own.x, y }, { x: part.x, y }, obstacles) === 0;
          break;
        }
        const y = n.r.y + d;
        if (!feasible(y)) break;
        n.r.y = y;
      }
      const after = affected.filter(isStraight).length;
      if (!ok || after <= before) n.r.y = oldY; // no net win — revert
    }
  }
}

/** Boxes an axis-aligned segment passes through. */
function segBoxCrossings(p1: GPoint, p2: GPoint, obstacles: readonly GRect[]): number {
  let n = 0;
  if (Math.abs(p1.y - p2.y) <= 1) {
    for (const r of obstacles) if (hCross(p1.y, p1.x, p2.x, r)) n += 1;
  } else if (Math.abs(p1.x - p2.x) <= 1) {
    for (const r of obstacles) if (vCross(p1.x, p1.y, p2.y, r)) n += 1;
  }
  return n;
}

/** Table boxes the edge's predicted path cuts through (straight edges scored
 *  on their single segment, bent ones on the renderer's 3-segment route). */
function edgeCrossings(
  e: GEdge,
  edges: GEdge[],
  getRect: (id: string) => GRect | undefined,
  obstaclesOf: (e: GEdge) => readonly GRect[],
): number {
  const r = predictRoute(e, edges, getRect);
  if (!r) return 0;
  const obstacles = obstaclesOf(e);
  if (r.straight) return segBoxCrossings(r.sp, r.tp, obstacles);
  if ((!r.horiz && !r.vert) || r.sSide === r.tSide) return 0;
  const horizontal = r.horiz;
  const a = horizontal ? r.sp.x : r.sp.y;
  const b = horizontal ? r.tp.x : r.tp.y;
  const center = bestCenter(r.sp, r.tp, horizontal, obstacles) ?? (a + b) / 2;
  return pathCrossings(r.sp, r.tp, horizontal, center, obstacles);
}

/** Ids of edges that currently render dead straight. */
function straightSet(comp: { nodes: LNode[]; edges: GEdge[] }): Set<string> {
  const rects = new Map(comp.nodes.map((n) => [n.id, n.r]));
  const getRect = (id: string) => rects.get(id);
  const out = new Set<string>();
  for (const e of comp.edges) {
    const r = predictRoute(e, comp.edges, getRect);
    if (r?.straight) out.add(e.id);
  }
  return out;
}

/**
 * Block compaction (whitespace removal). The alignment sweeps pull nodes to
 * wherever their partners sit, leaving large vertical holes between unrelated
 * groups. Nodes connected by *straight* edges form rigid blocks (translating
 * a block keeps its internal alignments); blocks are then pushed upward as
 * far as their x-overlapping predecessors allow — classic top-down compaction
 * that can only shrink the diagram, never grow it.
 */
function blockCompact(comp: { nodes: LNode[]; edges: GEdge[] }): void {
  if (comp.nodes.length <= 2) return;
  const straight = straightSet(comp);
  // Union blocks over straight edges.
  const parent = new Map<string, string>();
  const find = (x: string): string => {
    let r = x;
    while (parent.get(r) !== r) r = parent.get(r)!;
    parent.set(x, r);
    return r;
  };
  for (const n of comp.nodes) parent.set(n.id, n.id);
  for (const e of comp.edges) {
    if (!straight.has(e.id)) continue;
    parent.set(find(e.source), find(e.target));
  }
  const blocks = new Map<string, LNode[]>();
  for (const n of comp.nodes) {
    const r = find(n.id);
    (blocks.get(r) ?? blocks.set(r, []).get(r)!).push(n);
  }
  const boxes = [...blocks.values()].map((nodes) => ({
    nodes,
    minX: Math.min(...nodes.map((n) => n.r.x)),
    maxX: Math.max(...nodes.map((n) => n.r.x + n.r.w)),
    minY: Math.min(...nodes.map((n) => n.r.y)),
    maxY: Math.max(...nodes.map((n) => n.r.y + n.r.h)),
  }));
  const top = Math.min(...boxes.map((b) => b.minY));
  boxes.sort((a, b) => a.minY - b.minY);

  // Crossing-aware acceptance: a tuck that pulls the block (or its edges)
  // through other tables trades whitespace for line noise — score the edges
  // whose geometry the move affects and keep the move only when table
  // crossings don't increase (falling back to half/quarter shifts).
  const rects = new Map(comp.nodes.map((n) => [n.id, n.r]));
  const getRect = (id: string) => rects.get(id);
  const incident = new Map<string, GEdge[]>();
  for (const e of comp.edges) {
    if (e.source === e.target) continue;
    (incident.get(e.source) ?? incident.set(e.source, []).get(e.source)!).push(e);
    (incident.get(e.target) ?? incident.set(e.target, []).get(e.target)!).push(e);
  }
  const allObstaclesOf = (e: GEdge): GRect[] =>
    comp.nodes.filter((n) => n.id !== e.source && n.id !== e.target).map((n) => n.r);

  const placed: typeof boxes = [];
  for (const b of boxes) {
    let floor = top;
    for (const p of placed) {
      if (p.maxX > b.minX - 4 && b.maxX > p.minX - 4) floor = Math.max(floor, p.maxY + CROSS_GAP);
    }
    const fullShift = floor - b.minY; // ≤ 0 by construction (pure upward move)
    placed.push(b);
    if (fullShift >= -1) continue;
    // Edges whose path changes with this block: incident to its nodes or to
    // their partners (anchor redistribution reaches one hop out).
    const blockIds = new Set(b.nodes.map((n) => n.id));
    const touched = new Map<string, GEdge>();
    for (const n of b.nodes) {
      for (const e of incident.get(n.id) ?? []) {
        touched.set(e.id, e);
        const partner = e.source === n.id ? e.target : e.source;
        for (const pe of incident.get(partner) ?? []) touched.set(pe.id, pe);
      }
    }
    const blockRects = b.nodes.map((n) => n.r);
    const score = (): number => {
      let s = 0;
      for (const e of comp.edges) {
        if (e.source === e.target) continue;
        if (touched.has(e.id)) s += edgeCrossings(e, comp.edges, getRect, allObstaclesOf);
        else if (!blockIds.has(e.source) && !blockIds.has(e.target))
          s += edgeCrossings(e, comp.edges, getRect, () => blockRects);
      }
      return s;
    };
    const apply = (dy: number) => {
      for (const n of b.nodes) n.r.y += dy;
      b.minY += dy;
      b.maxY += dy;
    };
    const before = score();
    let applied = 0;
    for (const frac of [1, 0.5, 0.25]) {
      const dy = Math.round(fullShift * frac);
      if (dy >= -1) break;
      apply(dy - applied);
      applied = dy;
      if (score() <= before) break; // good tuck — keep it
      if (frac === 0.25) {
        apply(-applied); // even the smallest tuck adds crossings — revert
        applied = 0;
      }
    }
  }
}

/**
 * Per-gap rank spacing (whitespace removal, part 2). dagre only takes ONE
 * ranksep, so a single busy gap used to inflate every gap in the component.
 * Instead each gap is sized for the bent edges that actually route a middle
 * segment through it (straight edges need no width at all): base clearance
 * for the cardinality markers + label, plus one channel per bent edge.
 */
function gapRetune(comp: { nodes: LNode[]; edges: GEdge[] }): void {
  const ranks = ranksOf(comp.nodes);
  if (ranks.length <= 1) return;
  const rects = new Map(comp.nodes.map((n) => [n.id, n.r]));
  const getRect = (id: string) => rects.get(id);
  const rankIdx = new Map<string, number>();
  ranks.forEach((rank, i) => rank.forEach((n) => rankIdx.set(n.id, i)));
  const bent: number[] = new Array(ranks.length - 1).fill(0);
  for (const e of comp.edges) {
    const r = predictRoute(e, comp.edges, getRect);
    if (!r || r.straight) continue;
    const a = rankIdx.get(e.source) ?? 0;
    const b = rankIdx.get(e.target) ?? 0;
    if (a === b) continue;
    const span = Math.abs(b - a);
    // A skip-level edge puts its middle segment in only ONE of its gaps —
    // reserve fractional width so long spans don't inflate every gap.
    const share = span === 1 ? 1 : 1 / span;
    for (let g = Math.min(a, b); g < Math.max(a, b); g++) bent[g] += share;
  }
  const bbox = (rank: LNode[]) => ({
    left: Math.min(...rank.map((n) => n.r.x)),
    right: Math.max(...rank.map((n) => n.r.x + n.r.w)),
  });
  let cursor = bbox(ranks[0]).right;
  for (let i = 1; i < ranks.length; i++) {
    const gap = Math.min(
      RANK_GAP_MAX,
      Math.max(RANK_GAP_MIN, 56 + Math.ceil(bent[i - 1]) * (CHANNEL_GAP + 2)),
    );
    const { left, right } = bbox(ranks[i]);
    const shift = cursor + gap - left;
    for (const n of ranks[i]) n.r.x += shift;
    cursor = right + shift;
  }
}

/** Predicted route of one edge with the renderer's exact pipeline. */
interface PredictedRoute {
  edge: GEdge;
  sp: GPoint;
  tp: GPoint;
  straight: boolean;
  /** both ends on left/right sides → centerX is routable */
  horiz: boolean;
  /** both ends on top/bottom sides → centerY is routable */
  vert: boolean;
  sSide: Anchor["side"];
  tSide: Anchor["side"];
}

function predictRoute(
  e: GEdge,
  edges: GEdge[],
  getRect: (id: string) => GRect | undefined,
): PredictedRoute | null {
  if (e.source === e.target) return null;
  const sRect = getRect(e.source);
  const tRect = getRect(e.target);
  if (!sRect || !tRect) return null;
  const sa = distributeAnchor(e.source, e.target, e.id, edges, getRect);
  const ta = distributeAnchor(e.target, e.source, e.id, edges, getRect);
  const snapped = straightSnap({ x: sa.x, y: sa.y }, { x: ta.x, y: ta.y }, sa.side, ta.side, sRect, tRect);
  const horiz =
    (sa.side === "left" || sa.side === "right") && (ta.side === "left" || ta.side === "right");
  const vert =
    (sa.side === "top" || sa.side === "bottom") && (ta.side === "top" || ta.side === "bottom");
  return { edge: e, ...snapped, horiz, vert, sSide: sa.side, tSide: ta.side };
}

/**
 * Channel assignment: edges that stay bent get distinct middle-segment
 * positions — zero table crossings first, then ≥ CHANNEL_GAP from already
 * placed parallel segments, then closest to the natural midpoint. The choice
 * is persisted as relation.centerRatio (the renderer honors it verbatim), but
 * only when it actually beats what the renderer would do on its own.
 */
function assignChannels(comp: { nodes: LNode[]; edges: GEdge[] }): Map<string, number> {
  const ratios = new Map<string, number>();
  if (comp.edges.length === 0) return ratios;
  const rects = new Map(comp.nodes.map((n) => [n.id, n.r]));
  const getRect = (id: string) => rects.get(id);
  const routes = comp.edges
    .map((e) => predictRoute(e, comp.edges, getRect))
    // Same-side pairs (left→left etc.) take a wrap-around smoothstep the
    // 3-segment model below doesn't describe — leave those to the renderer.
    .filter((r): r is PredictedRoute => !!r && !r.straight && (r.horiz || r.vert) && r.sSide !== r.tSide);

  // Already-placed middle segments: {c, lo, hi} per orientation.
  const placedV: { c: number; lo: number; hi: number }[] = []; // vertical middles (horiz routes)
  const placedH: { c: number; lo: number; hi: number }[] = [];
  const overlap = (a1: number, a2: number, b1: number, b2: number) =>
    Math.min(Math.max(a1, a2), Math.max(b1, b2)) - Math.max(Math.min(a1, a2), Math.min(b1, b2)) > 4;

  // Short spans first: they have the least routing freedom.
  routes.sort(
    (a, b) =>
      Math.abs(a.horiz ? a.tp.x - a.sp.x : a.tp.y - a.sp.y) -
      Math.abs(b.horiz ? b.tp.x - b.sp.x : b.tp.y - b.sp.y),
  );

  for (const r of routes) {
    const horizontal = r.horiz;
    const a = horizontal ? r.sp.x : r.sp.y;
    const b = horizontal ? r.tp.x : r.tp.y;
    if (Math.abs(b - a) <= 24) continue; // renderer can't route these anyway
    const obstacles: GRect[] = comp.nodes
      .filter((n) => n.id !== r.edge.source && n.id !== r.edge.target)
      .map((n) => n.r);
    const placed = horizontal ? placedV : placedH;
    const span = horizontal ? ([Math.min(r.sp.y, r.tp.y), Math.max(r.sp.y, r.tp.y)] as const) : ([Math.min(r.sp.x, r.tp.x), Math.max(r.sp.x, r.tp.x)] as const);
    const cost = (c: number): number => {
      let collisions = 0;
      for (const p of placed) {
        if (Math.abs(p.c - c) < CHANNEL_GAP && overlap(span[0], span[1], p.lo, p.hi)) collisions += 1;
      }
      return (
        pathCrossings(r.sp, r.tp, horizontal, c, obstacles) * 1000 +
        collisions * 100 +
        (Math.abs(c - (a + b) / 2) / Math.abs(b - a)) * 8
      );
    };
    // What the renderer would do unaided (bestCenter or plain midpoint):
    const auto = bestCenter(r.sp, r.tp, horizontal, obstacles) ?? (a + b) / 2;
    const autoCost = cost(auto);
    let chosen = auto;
    let chosenCost = autoCost;
    for (let i = 0; i <= 20; i++) {
      const c = a + (b - a) * (0.1 + (0.8 * i) / 20);
      const s = cost(c);
      if (s < chosenCost - 1e-9) {
        chosen = c;
        chosenCost = s;
      }
    }
    placed.push({ c: chosen, lo: span[0], hi: span[1] });
    // Persist only when our channel genuinely improves on the renderer's own
    // choice — otherwise leave the relation clean (auto keeps adapting when
    // the user later drags nodes around).
    if (chosenCost < autoCost - 1e-9) {
      const ratio = (chosen - a) / (b - a);
      ratios.set(r.edge.id, Math.min(0.95, Math.max(0.05, ratio)));
    }
  }
  return ratios;
}

/** Lay out one connected component; returns its normalized bbox. */
function layoutComponent(comp: { nodes: LNode[]; edges: GEdge[] }): Cluster {
  if (comp.nodes.length === 1) {
    const n = comp.nodes[0];
    n.r.x = 0;
    n.r.y = 0;
    return { ...comp, w: n.r.w, h: n.r.h };
  }

  // Pass 1: provisional placement to discover real anchor sides per node.
  dagrePass(comp, RANK_GAP_MIN + 20);
  const rects = new Map(comp.nodes.map((n) => [n.id, n.r]));
  const getRect = (id: string) => rects.get(id);
  const sideCount = new Map<string, { left: number; right: number; top: number; bottom: number }>();
  const bump = (id: string, side: Anchor["side"]) => {
    const c = sideCount.get(id) ?? { left: 0, right: 0, top: 0, bottom: 0 };
    c[side] += 1;
    sideCount.set(id, c);
  };
  for (const e of comp.edges) {
    const s = getRect(e.source);
    const t = getRect(e.target);
    if (!s || !t) continue;
    bump(e.source, rectSide(s, borderIntersection(s, t)));
    bump(e.target, rectSide(t, borderIntersection(t, s)));
  }
  // Hub sizing: a side hosting k distributed anchors needs length ≥ (k+1)·GAP.
  for (const n of comp.nodes) {
    const c = sideCount.get(n.id);
    if (!c) continue;
    const needH = (Math.max(c.left, c.right) + 1) * EDGE_GAP;
    if (needH > n.r.h) n.r.h = needH;
    const needW = (Math.max(c.top, c.bottom) + 1) * EDGE_GAP;
    if (needW > n.r.w) n.r.w = needW;
  }
  // Pass 2 with final sizes, then alignment sweeps and straightness rescue.
  // Rank gaps are retuned per-gap afterwards (gapRetune), so dagre runs with
  // a uniform moderate ranksep here.
  dagrePass(comp, RANK_GAP_MIN + 40);
  alignSweeps(comp);
  rescuePass(comp);
  // Whitespace removal: pull straight-edge blocks together vertically, then
  // size each rank gap for the bent edges that actually route through it.
  blockCompact(comp);
  gapRetune(comp);
  // Geometry changed → a few alignments may sit just past the snap tolerance
  // again (anchor angles depend on the horizontal distance); re-rescue.
  rescuePass(comp);

  // Normalize to (0,0).
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const n of comp.nodes) {
    minX = Math.min(minX, n.r.x);
    minY = Math.min(minY, n.r.y);
    maxX = Math.max(maxX, n.r.x + n.r.w);
    maxY = Math.max(maxY, n.r.y + n.r.h);
  }
  for (const n of comp.nodes) {
    n.r.x -= minX;
    n.r.y -= minY;
  }
  return { ...comp, w: maxX - minX, h: maxY - minY };
}

/** Return a copy of ``design`` with tidied, non-overlapping positions and
 *  renderer-exact line optimization (sizes, anchors, bend channels). */
export function autoLayout(design: ErdDesign, dir: LayoutDirection = "TB"): ErdDesign {
  if (design.tables.length === 0) return design;
  const scale = design.fontScale ?? 1;
  const transpose = dir === "TB";

  // Rendered-pixel rects; content-fit width, formula height (manual sizes are
  // re-derived — auto-layout owns sizing, like it owns positions).
  const degree = new Map<string, number>();
  for (const r of design.relations) {
    if (r.from === r.to) continue;
    degree.set(r.from, (degree.get(r.from) ?? 0) + 1);
    degree.set(r.to, (degree.get(r.to) ?? 0) + 1);
  }
  const nodes: LNode[] = design.tables.map((t) => {
    const w = Math.round(fitWidth(t) * scale);
    const h = contentHeight(t.columns.length, scale);
    return {
      id: t.id,
      r: transpose ? { x: 0, y: 0, w: h, h: w } : { x: 0, y: 0, w, h },
      cols: t.columns.length,
      degree: degree.get(t.id) ?? 0,
    };
  });
  const edges: GEdge[] = design.relations.map((r) => ({ id: r.id, source: r.from, target: r.to }));

  const comps = components(nodes, edges);
  const laid = comps.map((c) => layoutComponent(c));
  const ratios = new Map<string, number>();
  for (const c of laid) for (const [id, v] of assignChannels(c)) ratios.set(id, v);

  // Keep connected clusters first (largest area first), singletons last so the
  // lone tables tuck neatly into a trailing grid instead of splitting clusters.
  const order = laid
    .map((l) => ({ l, single: l.nodes.length === 1, area: l.w * l.h }))
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
    for (const n of l.nodes) {
      pos.set(n.id, { x: Math.round(cursorX + n.r.x), y: Math.round(cursorY + n.r.y) });
    }
    cursorX += l.w + CLUSTER_GAP_X;
    rowHeight = Math.max(rowHeight, l.h);
  }

  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const tables = design.tables.map((t) => {
    const n = nodeById.get(t.id)!;
    const p = pos.get(t.id) ?? { x: t.x, y: t.y };
    // Un-transpose: in TB the layout's cross axis is the real x.
    const [x, y] = transpose ? [p.y, p.x] : [p.x, p.y];
    const [rw, rh] = transpose ? [n.r.h, n.r.w] : [n.r.w, n.r.h];
    const autoH = contentHeight(t.columns.length, scale);
    return {
      ...t,
      x,
      y,
      w: Math.round(rw / scale),
      // Explicit height only when the hub boost raised it above the content
      // height — otherwise stay auto so the node grows with new columns.
      h: rh > autoH ? Math.round(rh / scale) : undefined,
    };
  });

  // Auto-layout owns the routing: stale manual bends/anchors are dropped and
  // the channel assignment writes fresh centerRatios where they help.
  const relations: DesignRelation[] = design.relations.map((r) => ({
    ...r,
    centerRatio: ratios.get(r.id),
    sourceAnchor: undefined,
    targetAnchor: undefined,
  }));

  return { ...design, tables, relations };
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
      fontScale: design.fontScale,
    };
    const hasPositions = !!a.positions && Object.keys(a.positions).length >= Math.min(2, members.length);
    const laid = hasPositions ? removeOverlaps(sub) : autoLayout(sub);
    const positions: Record<string, { x: number; y: number }> = {};
    for (const t of laid.tables) positions[t.id] = { x: t.x, y: t.y };
    return { ...a, positions };
  });
  return { ...design, areas };
}

/* ── Routing diagnostics (used by the verification driver / future tests) ── */

export interface RoutingStats {
  edges: number;
  straight: number;
  /** edge runs cutting through unrelated table boxes */
  nodeCrossings: number;
  /** parallel middle segments closer than 6px with overlapping spans */
  segmentCollisions: number;
  /** overlapping table pairs */
  nodeOverlaps: number;
}

/** Predict every edge exactly as the renderer draws it and score the diagram. */
export function analyzeRouting(design: ErdDesign): RoutingStats {
  const scale = design.fontScale ?? 1;
  const rects = new Map<string, GRect>(
    design.tables.map((t) => [
      t.id,
      {
        x: t.x,
        y: t.y,
        w: Math.round((t.w ?? DEFAULT_W) * scale),
        h: t.h ? Math.round(t.h * scale) : contentHeight(t.columns.length, scale),
      },
    ]),
  );
  const getRect = (id: string) => rects.get(id);
  const edges: GEdge[] = design.relations.map((r) => ({ id: r.id, source: r.from, target: r.to }));
  const byId = new Map(design.relations.map((r) => [r.id, r]));

  let straight = 0;
  let nodeCrossings = 0;
  let segmentCollisions = 0;
  const middles: { horizontal: boolean; c: number; lo: number; hi: number }[] = [];

  for (const e of edges) {
    const r = predictRoute(e, edges, getRect);
    if (!r) continue;
    if (r.straight) {
      straight += 1;
      nodeCrossings += segBoxCrossings(
        r.sp,
        r.tp,
        design.tables.filter((t) => t.id !== e.source && t.id !== e.target).map((t) => rects.get(t.id)!),
      );
      continue;
    }
    if ((!r.horiz && !r.vert) || r.sSide === r.tSide) continue; // wrap-around smoothstep; not scored
    const horizontal = r.horiz;
    const a = horizontal ? r.sp.x : r.sp.y;
    const b = horizontal ? r.tp.x : r.tp.y;
    const obstacles = design.tables
      .filter((t) => t.id !== e.source && t.id !== e.target)
      .map((t) => rects.get(t.id)!);
    const rel = byId.get(e.id);
    const manual = rel?.centerRatio;
    const center =
      manual !== undefined && Math.abs(b - a) > 24
        ? a + (b - a) * manual
        : (bestCenter(r.sp, r.tp, horizontal, obstacles) ?? (a + b) / 2);
    nodeCrossings += pathCrossings(r.sp, r.tp, horizontal, center, obstacles);
    const lo = horizontal ? Math.min(r.sp.y, r.tp.y) : Math.min(r.sp.x, r.tp.x);
    const hi = horizontal ? Math.max(r.sp.y, r.tp.y) : Math.max(r.sp.x, r.tp.x);
    for (const m of middles) {
      if (m.horizontal === horizontal && Math.abs(m.c - center) < 6 && Math.min(hi, m.hi) - Math.max(lo, m.lo) > 4) {
        segmentCollisions += 1;
      }
    }
    middles.push({ horizontal, c: center, lo, hi });
  }

  let nodeOverlaps = 0;
  const list = [...rects.values()];
  for (let i = 0; i < list.length; i++) {
    for (let j = i + 1; j < list.length; j++) {
      const A = list[i];
      const B = list[j];
      if (A.x < B.x + B.w && B.x < A.x + A.w && A.y < B.y + B.h && B.y < A.y + A.h) nodeOverlaps += 1;
    }
  }
  return {
    edges: edges.filter((e) => e.source !== e.target).length,
    straight,
    nodeCrossings,
    segmentCollisions,
    nodeOverlaps,
  };
}
