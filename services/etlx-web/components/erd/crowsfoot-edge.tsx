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
}: EdgeProps) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  // Many (crow's foot) at the source end — three prongs converging to a
  // point ~16px out along the line, spreading back to the node edge.
  const foot =
    `M ${sourceX + 16},${sourceY} L ${sourceX},${sourceY - 7} ` +
    `M ${sourceX + 16},${sourceY} L ${sourceX},${sourceY} ` +
    `M ${sourceX + 16},${sourceY} L ${sourceX},${sourceY + 7}`;

  // One (bar) at the target end — a short perpendicular tick ~12px out.
  const bar = `M ${targetX - 12},${targetY - 7} L ${targetX - 12},${targetY + 7}`;

  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      <path d={foot} stroke={STROKE} strokeWidth={1.5} fill="none" />
      <path d={bar} stroke={STROKE} strokeWidth={1.5} fill="none" />
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
