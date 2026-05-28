/**
 * Hierarchical auto-layout for the pipeline builder graph (Phase O,
 * 2026-05-28). Wraps :pkg:`@dagrejs/dagre` so the builder doesn't have
 * to know about the layout algorithm internals — give it the current
 * nodes/edges, get back a new ``GraphBuilderState`` with positions
 * tidied into source→sink columns.
 *
 * Design choices worth noting:
 *
 *  * **Direction = LR.** Matches the existing builder's "source on
 *    the left, sink on the right" mental model (the React Flow handles
 *    are positioned for left-input / right-output already). Top-to-
 *    bottom would force the operator to mentally re-orient every node
 *    card.
 *  * **One node = one dagre vertex.** No virtual nodes, no compound
 *    layout — keeps the cycle time of a single-pass dagre under 10 ms
 *    for graphs of the size we expect (<100 nodes). Bigger graphs land
 *    with the same approach (dagre scales) until we hit perf issues.
 *  * **Node size is uniform.** PipelineNode renders at ``w-60`` (240 px)
 *    with a roughly 80 px header+summary stack. Hard-coding here keeps
 *    the helper independent of the visual layer; if the card grows we
 *    re-pass these constants and dagre re-spaces.
 *  * **Stable shape preservation.** ``data`` / ``operatorId`` / edges
 *    are passed through untouched — only ``position`` changes. So
 *    auto-layout never accidentally drops a config value, and undo
 *    after auto-layout restores the previous positions exactly.
 *
 *  Empty / no-op graphs return the input unchanged so the caller can
 *  always re-commit the result without checking.
 */
import dagre from "@dagrejs/dagre";

import type { GraphBuilderState } from "@/lib/pipeline-config";

// PipelineNode renders at w-60 (240 px). The visual height is variable
// (header + truncated summary + optional warning), but dagre needs a
// rectangle — pick a number that overshoots the typical card so columns
// stay readable instead of cramming.
const NODE_W = 240;
const NODE_H = 90;

// Spacing inside dagre. ``nodesep`` is between rank-mates (vertical
// gap), ``ranksep`` is between ranks (horizontal in LR). 80/110 gives
// a comfortable read without burning canvas real estate; matches the
// drop-cascade defaults the builder already uses for click-added nodes.
const NODE_SEP = 80;
const RANK_SEP = 110;

/** Return a copy of ``state`` with node ``position`` reflowed into a
 *  left-to-right layered layout. Edge order + node data untouched.
 *
 *  Caller responsibility: route the result through ``history.commit``
 *  so the layout becomes an undo target (Cmd+Z restores prior
 *  positions). Always-safe: zero-node graph returns the input
 *  reference; identity short-circuit means React.memo'd consumers
 *  skip the diff.
 */
export function autoLayoutGraph(state: GraphBuilderState): GraphBuilderState {
  if (state.nodes.length === 0) return state;

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: NODE_SEP, ranksep: RANK_SEP });
  // Required for dagre but unused — we don't carry per-edge labels.
  g.setDefaultEdgeLabel(() => ({}));

  for (const node of state.nodes) {
    g.setNode(node.id, { width: NODE_W, height: NODE_H });
  }
  for (const edge of state.edges) {
    // dagre tolerates dangling edges (one endpoint missing) — but our
    // graph builder guarantees both endpoints exist, so this is a
    // straight pass-through.
    g.setEdge(edge.source, edge.target);
  }

  dagre.layout(g);

  return {
    ...state,
    nodes: state.nodes.map((node) => {
      const laid = g.node(node.id);
      if (!laid) return node;
      // dagre reports the centre point of the rectangle; React Flow's
      // position is the top-left corner. Offset by half the size so
      // the visual centre lands where dagre intended.
      return {
        ...node,
        position: {
          x: laid.x - NODE_W / 2,
          y: laid.y - NODE_H / 2,
        },
      };
    }),
  };
}
