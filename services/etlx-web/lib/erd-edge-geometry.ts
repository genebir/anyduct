/**
 * Shared ERD edge geometry (Phase ALB).
 *
 * The crow's-foot edge renderer decides — at render time — where each
 * relationship attaches to a table (facing side + sibling distribution),
 * whether it snaps dead straight, and where the orthogonal bend sits.
 * For the auto-layout to *optimize* those lines (align attachment points,
 * keep middle segments apart, avoid cutting through tables) it must predict
 * them exactly. This module is that single source of truth: pure functions
 * over plain rects, used by BOTH the renderer (crowsfoot-edge.tsx) and the
 * layout engine (erd-layout.ts). If routing behavior changes, change it here.
 */

export type Side = "left" | "right" | "top" | "bottom";

export interface GRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface GPoint {
  x: number;
  y: number;
}

export interface GEdge {
  id: string;
  source: string;
  target: string;
}

/** Border point of ``node`` along the line toward ``other``'s center. */
export function borderIntersection(node: GRect, other: GRect): GPoint {
  const w = node.w / 2;
  const h = node.h / 2;
  const cx = node.x + w;
  const cy = node.y + h;
  const ox = other.x + other.w / 2;
  const oy = other.y + other.h / 2;
  const xx = (ox - cx) / (2 * w) - (oy - cy) / (2 * h);
  const yy = (ox - cx) / (2 * w) + (oy - cy) / (2 * h);
  const a = 1 / (Math.abs(xx) + Math.abs(yy) || 1);
  const sx = a * xx;
  const sy = a * yy;
  return { x: w * (sx + sy) + cx, y: h * (-sx + sy) + cy };
}

/** Which side of ``node`` the point sits on. */
export function rectSide(node: GRect, p: GPoint): Side {
  if (p.x <= node.x + 1) return "left";
  if (p.x >= node.x + node.w - 1) return "right";
  if (p.y <= node.y + 1) return "top";
  return "bottom";
}

/** Outward unit normal for a side. */
export function sideNormal(side: Side): { nx: number; ny: number } {
  switch (side) {
    case "left":
      return { nx: -1, ny: 0 };
    case "right":
      return { nx: 1, ny: 0 };
    case "top":
      return { nx: 0, ny: -1 };
    default:
      return { nx: 0, ny: 1 };
  }
}

/** Point on ``node``'s border for an explicit side + ratio along it. */
export function anchorAt(node: GRect, side: Side, t: number): GPoint {
  switch (side) {
    case "left":
      return { x: node.x, y: node.y + node.h * t };
    case "right":
      return { x: node.x + node.w, y: node.y + node.h * t };
    case "top":
      return { x: node.x + node.w * t, y: node.y };
    default:
      return { x: node.x + node.w * t, y: node.y + node.h };
  }
}

export interface Anchor extends GPoint {
  side: Side;
}

/**
 * Anchor with sibling spacing (Phase AKR). When several relationships attach
 * to the SAME side of a node they used to converge on (almost) one border
 * point and read as a single thick line. Instead, collect the edges sharing
 * this side, order them by where the opposite table sits (minimizes
 * crossings), and distribute the anchors evenly along the side.
 *
 * ``getRect`` resolves a node id to its current rect (renderer: live React
 * Flow geometry; layout: the in-progress placement).
 */
export function distributeAnchor(
  nodeId: string,
  otherId: string,
  edgeId: string,
  edges: readonly GEdge[],
  getRect: (id: string) => GRect | undefined,
): Anchor {
  const node = getRect(nodeId);
  const other = getRect(otherId);
  if (!node || !other) return { x: 0, y: 0, side: "left" };
  const p = borderIntersection(node, other);
  const side = rectSide(node, p);
  const horiz = side === "top" || side === "bottom";
  const sibs: { id: string; t: number }[] = [];
  for (const e of edges) {
    if (e.source === e.target) continue;
    const oid = e.source === nodeId ? e.target : e.target === nodeId ? e.source : null;
    if (!oid) continue;
    const o = getRect(oid);
    if (!o) continue;
    if (rectSide(node, borderIntersection(node, o)) !== side) continue;
    const oc = horiz ? o.x + o.w / 2 : o.y + o.h / 2;
    sibs.push({ id: e.id, t: oc });
  }
  if (sibs.length <= 1) return { x: p.x, y: p.y, side };
  sibs.sort((a, b) => a.t - b.t || a.id.localeCompare(b.id));
  const idx = sibs.findIndex((s) => s.id === edgeId);
  if (idx < 0) return { x: p.x, y: p.y, side };
  const frac = (idx + 1) / (sibs.length + 1);
  return { ...anchorAt(node, side, frac), side };
}

/* ── Route prediction ──────────────────────────────────────────────────── */

/** Snap tolerance for nearly-aligned anchors → dead-straight line (AKZ). */
export const SNAP = 14;

export interface RouteEndpoints {
  sp: GPoint;
  tp: GPoint;
  straight: boolean;
}

/**
 * Bend minimisation (Phase AKZ): nearly-aligned anchors snap to a dead-
 * straight line — most of the visual noise comes from tiny S-bends. Mutates
 * copies of the endpoints; never the inputs.
 */
export function straightSnap(
  sp: GPoint,
  tp: GPoint,
  sSide: Side,
  tSide: Side,
  srcRect: GRect,
  tgtRect: GRect,
): RouteEndpoints {
  const s = { ...sp };
  const t = { ...tp };
  const horizRoute = (sSide === "left" || sSide === "right") && (tSide === "left" || tSide === "right");
  const vertRoute = (sSide === "top" || sSide === "bottom") && (tSide === "top" || tSide === "bottom");
  if (horizRoute && Math.abs(s.y - t.y) <= SNAP) {
    const lo = Math.max(srcRect.y + 10, tgtRect.y + 10);
    const hi = Math.min(srcRect.y + srcRect.h - 10, tgtRect.y + tgtRect.h - 10);
    if (lo < hi) {
      const y = Math.min(Math.max((s.y + t.y) / 2, lo), hi);
      s.y = y;
      t.y = y;
      return { sp: s, tp: t, straight: true };
    }
  } else if (vertRoute && Math.abs(s.x - t.x) <= SNAP) {
    const lo = Math.max(srcRect.x + 10, tgtRect.x + 10);
    const hi = Math.min(srcRect.x + srcRect.w - 10, tgtRect.x + tgtRect.w - 10);
    if (lo < hi) {
      const x = Math.min(Math.max((s.x + t.x) / 2, lo), hi);
      s.x = x;
      t.x = x;
      return { sp: s, tp: t, straight: true };
    }
  }
  return { sp: s, tp: t, straight: false };
}

/** Clearance around nodes when testing path/box crossings. */
export const ROUTE_PAD = 6;

export function hCross(y: number, x1: number, x2: number, r: GRect, pad = ROUTE_PAD): boolean {
  const lo = Math.min(x1, x2);
  const hi = Math.max(x1, x2);
  return y >= r.y - pad && y <= r.y + r.h + pad && hi >= r.x - pad && lo <= r.x + r.w + pad;
}

export function vCross(x: number, y1: number, y2: number, r: GRect, pad = ROUTE_PAD): boolean {
  const lo = Math.min(y1, y2);
  const hi = Math.max(y1, y2);
  return x >= r.x - pad && x <= r.x + r.w + pad && hi >= r.y - pad && lo <= r.y + r.h + pad;
}

/** How many node boxes the 3-segment orthogonal path with center ``c`` crosses. */
export function pathCrossings(
  sp: GPoint,
  tp: GPoint,
  horizontal: boolean,
  c: number,
  rects: readonly GRect[],
): number {
  let n = 0;
  for (const r of rects) {
    if (horizontal) {
      if (hCross(sp.y, sp.x, c, r)) n += 1;
      if (vCross(c, sp.y, tp.y, r)) n += 1;
      if (hCross(tp.y, c, tp.x, r)) n += 1;
    } else {
      if (vCross(sp.x, sp.y, c, r)) n += 1;
      if (hCross(c, sp.x, tp.x, r)) n += 1;
      if (vCross(tp.x, c, tp.y, r)) n += 1;
    }
  }
  return n;
}

/**
 * Obstacle-aware bend placement (Phase AKS). Scan candidate center positions
 * between the two anchors, count how many node boxes the 3-segment orthogonal
 * path would cross, and keep the candidate with the fewest crossings
 * (preferring the one closest to the middle). ``undefined`` = default mid is
 * already clean (or the span is too short to route).
 */
export function bestCenter(
  sp: GPoint,
  tp: GPoint,
  horizontal: boolean,
  rects: readonly GRect[],
): number | undefined {
  const a = horizontal ? sp.x : sp.y;
  const b = horizontal ? tp.x : tp.y;
  if (Math.abs(b - a) < 24 || rects.length === 0) return undefined;
  const mid = (a + b) / 2;
  let best: number | undefined;
  let bestScore = pathCrossings(sp, tp, horizontal, mid, rects);
  if (bestScore === 0) return undefined; // default is already clean
  for (let i = 1; i <= 9; i++) {
    const c = a + ((b - a) * i) / 10;
    const s = pathCrossings(sp, tp, horizontal, c, rects);
    if (s < bestScore || (s === bestScore && best !== undefined && Math.abs(c - mid) < Math.abs(best - mid))) {
      bestScore = s;
      best = c;
    }
  }
  return best;
}
