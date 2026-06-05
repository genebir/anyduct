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

  const sp = intersection(sourceNode, targetNode);
  const tp = intersection(targetNode, sourceNode);
  const sPos = sideOf(sourceNode, sp);
  const tPos = sideOf(targetNode, tp);
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX: sp.x,
    sourceY: sp.y,
    sourcePosition: sPos,
    targetX: tp.x,
    targetY: tp.y,
    targetPosition: tPos,
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
