"use client";

/**
 * Crow's-foot (information-engineering) ERD edge markers (Phase AGY).
 *
 * Classic ERD relationship lines show cardinality at each end: a single
 * bar = "one", a crow's foot = "many". A foreign key A.<x>_id → B means
 * "many A reference one B", so the edge is drawn:
 *
 *     B (one) │———< A (many)
 *
 * In our model an edge is ``source = from`` (the table holding the FK,
 * the MANY side) → ``target = to`` (the referenced table, the ONE side).
 * So ``markerStart`` (source) is the crow's foot and ``markerEnd``
 * (target) is the one-bar. Markers use ``orient="auto-start-reverse"`` so
 * the same definition orients correctly at either end.
 *
 * Render <ErdMarkers/> once inside the ReactFlow wrapper; reference the
 * markers from edges via the exported url() constants.
 */

export const ERD_MARKER_MANY = "url(#erd-crowsfoot-many)";
export const ERD_MARKER_ONE = "url(#erd-crowsfoot-one)";
const STROKE = "rgb(var(--accent))";

export function ErdMarkers() {
  return (
    <svg
      aria-hidden
      style={{ position: "absolute", width: 0, height: 0, overflow: "hidden" }}
    >
      <defs>
        {/* "one" — a single perpendicular bar near the entity. */}
        <marker
          id="erd-crowsfoot-one"
          markerWidth="22"
          markerHeight="22"
          refX="18"
          refY="11"
          orient="auto-start-reverse"
          markerUnits="userSpaceOnUse"
        >
          <path d="M13 4 L13 18" stroke={STROKE} strokeWidth="1.5" fill="none" />
        </marker>
        {/* "many" — crow's foot: one point fanning to three at the entity. */}
        <marker
          id="erd-crowsfoot-many"
          markerWidth="22"
          markerHeight="22"
          refX="19"
          refY="11"
          orient="auto-start-reverse"
          markerUnits="userSpaceOnUse"
        >
          <path
            d="M3 11 L19 4 M3 11 L19 11 M3 11 L19 18"
            stroke={STROKE}
            strokeWidth="1.3"
            fill="none"
          />
        </marker>
      </defs>
    </svg>
  );
}
