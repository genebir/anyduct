"use client";

/**
 * Crow's-foot ERD edge (Phase AGZ) — draws the cardinality symbols as
 * part of the edge SVG instead of relying on ``url(#marker)`` refs (which
 * didn't render reliably inside @xyflow/react). Full control = guaranteed
 * to show.
 *
 * Nodes use sourcePosition=Right / targetPosition=Left, so the line is
 * horizontal at both ends: the **many** crow's foot sits just off the
 * source (FK side), the **one** bar just off the target (referenced
 * side). A foreign key A.<x>_id → B reads "B (one) │——< A (many)".
 */

import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type EdgeProps } from "@xyflow/react";

const STROKE = "rgb(var(--accent))";

/** Crow's foot (many) at the source end — prongs open toward the node. */
function sourceFoot(x: number, y: number): string {
  return (
    `M ${x + 16},${y} L ${x},${y - 7} ` +
    `M ${x + 16},${y} L ${x},${y} ` +
    `M ${x + 16},${y} L ${x},${y + 7}`
  );
}
/** Crow's foot (many) at the target end — prongs open toward the node. */
function targetFoot(x: number, y: number): string {
  return (
    `M ${x - 16},${y} L ${x},${y - 7} ` +
    `M ${x - 16},${y} L ${x},${y} ` +
    `M ${x - 16},${y} L ${x},${y + 7}`
  );
}
/** "one" bar near an end. */
function bar(x: number, y: number, dir: 1 | -1): string {
  const bx = x + dir * 12;
  return `M ${bx},${y - 7} L ${bx},${y + 7}`;
}

export function CrowsFootEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  label,
  style,
  data,
}: EdgeProps) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const sourceCard = (data?.sourceCard as string) ?? "many";
  const targetCard = (data?.targetCard as string) ?? "one";
  const sourceMark = sourceCard === "many" ? sourceFoot(sourceX, sourceY) : bar(sourceX, sourceY, 1);
  const targetMark = targetCard === "many" ? targetFoot(targetX, targetY) : bar(targetX, targetY, -1);

  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      <path d={sourceMark} stroke={STROKE} strokeWidth={1.5} fill="none" />
      <path d={targetMark} stroke={STROKE} strokeWidth={1.5} fill="none" />
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
