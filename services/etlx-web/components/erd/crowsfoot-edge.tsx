"use client";

/**
 * Floating crow's-foot ERD edge (Phase AHO).
 *
 * Each end attaches to the point on the table's border that faces the other
 * table (computed from live node geometry), and the cardinality marker is
 * oriented to that side. Because connection points follow the nodes rather
 * than a fixed Left/Right handle, lines don't pile up on top of each other
 * after auto-layout or dragging. Self-references draw a small loop.
 */

import {
  BaseEdge,
  EdgeLabelRenderer,
  Position,
  getSmoothStepPath,
  useInternalNode,
  useStore,
  type Edge,
  type EdgeProps,
  type InternalNode,
} from "@xyflow/react";

const STROKE = "rgb(var(--accent))";
const FOOT = 16; // crow's-foot depth
const SPREAD = 7; // crow's-foot half-width
const BAR = 11; // "one" bar offset from the border

/** Border point of ``node`` along the line toward ``other``'s center. */
function intersection(node: InternalNode, other: InternalNode): { x: number; y: number } {
  const w = (node.measured.width ?? 220) / 2;
  const h = (node.measured.height ?? 80) / 2;
  const cx = node.internals.positionAbsolute.x + w;
  const cy = node.internals.positionAbsolute.y + h;
  const ox = other.internals.positionAbsolute.x + (other.measured.width ?? 220) / 2;
  const oy = other.internals.positionAbsolute.y + (other.measured.height ?? 80) / 2;
  const xx = (ox - cx) / (2 * w) - (oy - cy) / (2 * h);
  const yy = (ox - cx) / (2 * w) + (oy - cy) / (2 * h);
  const a = 1 / (Math.abs(xx) + Math.abs(yy) || 1);
  const sx = a * xx;
  const sy = a * yy;
  return { x: w * (sx + sy) + cx, y: h * (-sx + sy) + cy };
}

/** Which side of ``node`` the point sits on. */
function sideOf(node: InternalNode, p: { x: number; y: number }): Position {
  const nx = node.internals.positionAbsolute.x;
  const ny = node.internals.positionAbsolute.y;
  const w = node.measured.width ?? 220;
  const h = node.measured.height ?? 80;
  if (p.x <= nx + 1) return Position.Left;
  if (p.x >= nx + w - 1) return Position.Right;
  if (p.y <= ny + 1) return Position.Top;
  return Position.Bottom;
}

/** Outward unit normal for a side. */
function normal(pos: Position): { nx: number; ny: number } {
  switch (pos) {
    case Position.Left:
      return { nx: -1, ny: 0 };
    case Position.Right:
      return { nx: 1, ny: 0 };
    case Position.Top:
      return { nx: 0, ny: -1 };
    default:
      return { nx: 0, ny: 1 };
  }
}


/**
 * Anchor with sibling spacing (Phase AKR). When several relationships attach
 * to the SAME side of a node they used to converge on (almost) one border
 * point and read as a single thick line. Instead, collect the edges sharing
 * this side, order them by where the opposite table sits (minimizes
 * crossings), and distribute the anchors evenly along the side.
 */
function distributedAnchor(
  node: InternalNode,
  other: InternalNode,
  edgeId: string,
  edges: Edge[],
  lookup: Map<string, InternalNode>,
): { x: number; y: number; pos: Position } {
  const p = intersection(node, other);
  const pos = sideOf(node, p);
  const horiz = pos === Position.Top || pos === Position.Bottom;
  const sibs: { id: string; t: number }[] = [];
  for (const e of edges) {
    if (e.source === e.target) continue;
    const otherId = e.source === node.id ? e.target : e.target === node.id ? e.source : null;
    if (!otherId) continue;
    const o = lookup.get(otherId);
    if (!o) continue;
    if (sideOf(node, intersection(node, o)) !== pos) continue;
    const oc = horiz
      ? o.internals.positionAbsolute.x + (o.measured.width ?? 220) / 2
      : o.internals.positionAbsolute.y + (o.measured.height ?? 80) / 2;
    sibs.push({ id: e.id, t: oc });
  }
  if (sibs.length <= 1) return { x: p.x, y: p.y, pos };
  sibs.sort((a, b) => a.t - b.t || a.id.localeCompare(b.id));
  const idx = sibs.findIndex((s) => s.id === edgeId);
  if (idx < 0) return { x: p.x, y: p.y, pos };
  const nx = node.internals.positionAbsolute.x;
  const ny = node.internals.positionAbsolute.y;
  const w = node.measured.width ?? 220;
  const h = node.measured.height ?? 80;
  const frac = (idx + 1) / (sibs.length + 1);
  switch (pos) {
    case Position.Left:
      return { x: nx, y: ny + h * frac, pos };
    case Position.Right:
      return { x: nx + w, y: ny + h * frac, pos };
    case Position.Top:
      return { x: nx + w * frac, y: ny, pos };
    default:
      return { x: nx + w * frac, y: ny + h, pos };
  }
}


/**
 * Obstacle-aware bend placement (Phase AKS). A smooth-step path bends at a
 * center line; with the default midpoint that middle segment (and the runs
 * leading to it) often slices straight through an unrelated table. Scan
 * candidate center positions between the two anchors, count how many node
 * boxes the 3-segment orthogonal path would cross, and keep the candidate
 * with the fewest crossings (preferring the one closest to the middle).
 */
interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}
const PAD = 6; // clearance around nodes

function hCross(y: number, x1: number, x2: number, r: Rect): boolean {
  const lo = Math.min(x1, x2);
  const hi = Math.max(x1, x2);
  return y >= r.y - PAD && y <= r.y + r.h + PAD && hi >= r.x - PAD && lo <= r.x + r.w + PAD;
}
function vCross(x: number, y1: number, y2: number, r: Rect): boolean {
  const lo = Math.min(y1, y2);
  const hi = Math.max(y1, y2);
  return x >= r.x - PAD && x <= r.x + r.w + PAD && hi >= r.y - PAD && lo <= r.y + r.h + PAD;
}

function bestCenter(
  sp: { x: number; y: number },
  tp: { x: number; y: number },
  horizontal: boolean, // true → vertical middle segment at centerX
  rects: Rect[],
): number | undefined {
  const a = horizontal ? sp.x : sp.y;
  const b = horizontal ? tp.x : tp.y;
  if (Math.abs(b - a) < 24 || rects.length === 0) return undefined;
  const crossings = (c: number): number => {
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
  };
  const mid = (a + b) / 2;
  let best: number | undefined;
  let bestScore = crossings(mid);
  if (bestScore === 0) return undefined; // default is already clean
  for (let i = 1; i <= 9; i++) {
    const c = a + ((b - a) * i) / 10;
    const s = crossings(c);
    if (s < bestScore || (s === bestScore && best !== undefined && Math.abs(c - mid) < Math.abs(best - mid))) {
      bestScore = s;
      best = c;
    }
  }
  return best;
}

/** Crow's foot (many): three prongs from an apex out along the normal back
 *  to the border, spread perpendicular to the normal. */
function foot(x: number, y: number, pos: Position): string {
  const { nx, ny } = normal(pos);
  const ax = x + nx * FOOT;
  const ay = y + ny * FOOT;
  // perpendicular
  const px = -ny;
  const py = nx;
  return (
    `M ${ax},${ay} L ${x + px * SPREAD},${y + py * SPREAD} ` +
    `M ${ax},${ay} L ${x},${y} ` +
    `M ${ax},${ay} L ${x - px * SPREAD},${y - py * SPREAD}`
  );
}

/** "one" bar: a tick perpendicular to the side, set off from the border. */
function oneBar(x: number, y: number, pos: Position): string {
  const { nx, ny } = normal(pos);
  const bx = x + nx * BAR;
  const by = y + ny * BAR;
  const px = -ny;
  const py = nx;
  return `M ${bx + px * SPREAD},${by + py * SPREAD} L ${bx - px * SPREAD},${by - py * SPREAD}`;
}

function mark(card: string, x: number, y: number, pos: Position): string {
  return card === "many" ? foot(x, y, pos) : oneBar(x, y, pos);
}

export function CrowsFootEdge({ id, source, target, label, style, data }: EdgeProps) {
  const sourceNode = useInternalNode(source);
  const targetNode = useInternalNode(target);
  const edges = useStore((s) => s.edges);
  const nodeLookup = useStore((s) => s.nodeLookup);
  if (!sourceNode || !targetNode) return null;

  const sourceCard = (data?.sourceCard as string) ?? "many";
  const targetCard = (data?.targetCard as string) ?? "one";

  // Self-reference: draw a small loop off the right side.
  if (source === target) {
    const w = sourceNode.measured.width ?? 220;
    const h = sourceNode.measured.height ?? 80;
    const rx = sourceNode.internals.positionAbsolute.x + w;
    const ry = sourceNode.internals.positionAbsolute.y;
    const y1 = ry + h * 0.3;
    const y2 = ry + h * 0.6;
    const bulge = 46;
    const path = `M ${rx},${y1} C ${rx + bulge},${y1} ${rx + bulge},${y2} ${rx},${y2}`;
    return (
      <>
        <BaseEdge id={id} path={path} style={style} />
        <path d={mark(sourceCard, rx, y1, Position.Right)} stroke={STROKE} strokeWidth={1.5} fill="none" />
        <path d={mark(targetCard, rx, y2, Position.Right)} stroke={STROKE} strokeWidth={1.5} fill="none" />
        {label ? (
          <EdgeLabelRenderer>
            <div
              style={{
                position: "absolute",
                transform: `translate(0, -50%) translate(${rx + bulge}px, ${(y1 + y2) / 2}px)`,
                pointerEvents: "none",
              }}
              className="rounded bg-bg/80 px-1 font-mono text-[10px] text-text-muted"
            >
              {label}
            </div>
          </EdgeLabelRenderer>
        ) : null}
      </>
    );
  }

  const sa = distributedAnchor(sourceNode, targetNode, id, edges, nodeLookup);
  const ta = distributedAnchor(targetNode, sourceNode, id, edges, nodeLookup);
  const sp = { x: sa.x, y: sa.y };
  const tp = { x: ta.x, y: ta.y };
  const sPos = sa.pos;
  const tPos = ta.pos;
  // Move the bend off any table the default midpoint path would cut through.
  const obstacles: Rect[] = [];
  for (const [nid, n] of nodeLookup) {
    if (nid === source || nid === target) continue;
    if (n.type === "shape") continue; // background boxes may be crossed
    obstacles.push({
      x: n.internals.positionAbsolute.x,
      y: n.internals.positionAbsolute.y,
      w: n.measured.width ?? 220,
      h: n.measured.height ?? 80,
    });
  }
  const horizRoute =
    (sPos === Position.Left || sPos === Position.Right) &&
    (tPos === Position.Left || tPos === Position.Right);
  const vertRoute =
    (sPos === Position.Top || sPos === Position.Bottom) &&
    (tPos === Position.Top || tPos === Position.Bottom);
  const center = horizRoute
    ? bestCenter(sp, tp, true, obstacles)
    : vertRoute
      ? bestCenter(sp, tp, false, obstacles)
      : undefined;
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX: sp.x,
    sourceY: sp.y,
    sourcePosition: sPos,
    targetX: tp.x,
    targetY: tp.y,
    targetPosition: tPos,
    ...(horizRoute && center !== undefined ? { centerX: center } : {}),
    ...(vertRoute && center !== undefined ? { centerY: center } : {}),
  });

  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      <path d={mark(sourceCard, sp.x, sp.y, sPos)} stroke={STROKE} strokeWidth={1.5} fill="none" />
      <path d={mark(targetCard, tp.x, tp.y, tPos)} stroke={STROKE} strokeWidth={1.5} fill="none" />
      {label ? (
        <EdgeLabelRenderer>
          <div
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: "none",
            }}
            className="rounded bg-bg/80 px-1 font-mono text-[10px] text-text-muted"
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

/** Stable edgeTypes map for ReactFlow. */
export const ERD_EDGE_TYPES = { crowsfoot: CrowsFootEdge };
