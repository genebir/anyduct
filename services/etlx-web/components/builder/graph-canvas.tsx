"use client";

import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect } from "react";
import { PipelineNode, type PipelineNodeData } from "./pipeline-node";
import { findOperator } from "@/lib/operators";
import { PALETTE_DND_MIME } from "./palette";
import type { GraphBuilderEdge, GraphBuilderNode } from "@/lib/pipeline-config";

const NODE_TYPES = { pipelineNode: PipelineNode };

/** Adding source→target keeps the graph well-formed (ADR-0030 v1 + ADR-0041
 *  G2/I1 multi-source + join fan-in):
 *
 *  * no edge into a source node;
 *  * non-join nodes take ≤1 incoming edge (transform / sink semantics);
 *  * join nodes (``multiInput`` operators) accept ≥2 inputs, so fan-in is OK;
 *  * no duplicate edge between the same pair;
 *  * no cycles.
 */
function connectionAllowed(
  nodes: GraphBuilderNode[],
  edges: GraphBuilderEdge[],
  source: string,
  target: string,
): boolean {
  if (source === target) return false;
  const tgt = nodes.find((n) => n.id === target);
  const tgtOp = findOperator(tgt?.operatorId ?? "");
  if (tgtOp?.kind === "source") return false; // no edge into a source
  // Fan-in: only join (multiInput) nodes accept ≥2 incoming edges. For every
  // other kind, we still enforce the "one incoming edge" rule so semantics
  // stay unambiguous (single-input stream per transform/sink).
  if (!tgtOp?.multiInput && edges.some((e) => e.target === target)) return false;
  if (edges.some((e) => e.source === source && e.target === target)) return false; // dup
  // cycle: is `source` reachable from `target`?
  const adj = new Map<string, string[]>();
  for (const e of edges) adj.set(e.source, [...(adj.get(e.source) ?? []), e.target]);
  const stack = [target];
  const seen = new Set<string>();
  while (stack.length) {
    const cur = stack.pop()!;
    if (cur === source) return false;
    if (seen.has(cur)) continue;
    seen.add(cur);
    stack.push(...(adj.get(cur) ?? []));
  }
  return true;
}

export interface GraphCanvasProps {
  nodes: GraphBuilderNode[];
  edges: GraphBuilderEdge[];
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onRemoveNode: (id: string) => void;
  /** Bulk delete from React Flow's built-in Delete/Backspace handler.
   *  Called once per delete with all selected node ids — keeps the
   *  undo stack to a single snapshot for a 10-node bulk delete. */
  onRemoveNodes?: (ids: string[]) => void;
  onConnect: (source: string, target: string) => void;
  onRemoveEdge: (id: string) => void;
  onMoveNode: (id: string, pos: { x: number; y: number }) => void;
  /** Reports the current multi-selection back up so the editor can
   *  service Cmd+D (duplicate) / Backspace (bulk delete) globally. */
  onSelectionChange?: (selection: { nodeIds: string[]; edgeIds: string[] }) => void;
  onDeselect?: () => void;
  /** Called when a palette item is dropped onto the canvas. ``position`` is
   *  the React-Flow coordinate under the cursor (already converted from
   *  screen pixels via ``screenToFlowPosition``). */
  onDropOperator?: (operatorId: string, position: { x: number; y: number }) => void;
  /** Right-click on empty canvas. ``flowPosition`` lets the caller's menu
   *  drop a new node at the click point. */
  onPaneContextMenu?: (
    event: { clientX: number; clientY: number },
    flowPosition: { x: number; y: number },
  ) => void;
  onNodeContextMenu?: (
    event: { clientX: number; clientY: number },
    nodeId: string,
  ) => void;
  onEdgeContextMenu?: (
    event: { clientX: number; clientY: number },
    edgeId: string,
  ) => void;
}

export function GraphCanvas(props: GraphCanvasProps) {
  return (
    // ReactFlowProvider stays at the *outer* wrapper because the inner
    // component (where dnd / context menus live) uses ``useReactFlow`` to
    // convert screen pixels into flow coordinates. The provider supplies
    // that hook's context.
    <ReactFlowProvider>
      <GraphCanvasInner {...props} />
    </ReactFlowProvider>
  );
}

function GraphCanvasInner({
  nodes,
  edges,
  selectedNodeId,
  selectedEdgeId,
  onSelectNode,
  onSelectEdge,
  onRemoveNode,
  onRemoveNodes,
  onConnect,
  onRemoveEdge,
  onMoveNode,
  onSelectionChange,
  onDeselect,
  onDropOperator,
  onPaneContextMenu,
  onNodeContextMenu,
  onEdgeContextMenu,
}: GraphCanvasProps) {
  const rf: ReactFlowInstance = useReactFlow();
  const layoutNodes: Node<PipelineNodeData>[] = nodes.map((n) => {
    const op = findOperator(n.operatorId);
    return {
      id: n.id,
      type: "pipelineNode",
      position: n.position,
      data: {
        operatorId: n.operatorId,
        values: n.data,
        selected: selectedNodeId === n.id,
        onSelect: onSelectNode,
        onRemove: onRemoveNode,
        // Sources/sinks aren't deletable via the node chrome here unless extra;
        // keep all nodes removable in free-form mode.
        canRemove: op?.kind !== undefined,
      },
    };
  });

  // Edge labels are ALWAYS visible (Phase L1 audit finding 2026-05-26):
  // branch conditions were buried — analysts thought every edge passed
  // every record. Now an unconditional edge reads "All records" and a
  // conditional edge reads "if <expr>", so the branching capability is
  // discoverable at a glance and the active condition is right there.
  // Conditional edges also get a slightly different accent so they
  // stand out against the default `All records` ones.
  const layoutEdges: Edge[] = edges.map((e) => {
    const conditional = Boolean(e.when);
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      animated: true,
      label: conditional ? `if ${e.when}` : "All records",
      labelStyle: {
        fill: conditional ? "rgb(var(--accent))" : "rgb(var(--text-muted))",
        fontSize: 11,
        fontWeight: conditional ? 600 : 400,
        cursor: "pointer",
      },
      labelBgPadding: [4, 2] as [number, number],
      labelBgBorderRadius: 4,
      labelBgStyle: { fill: "rgb(var(--bg-surface))" },
      selected: selectedEdgeId === e.id,
      style: {
        stroke:
          selectedEdgeId === e.id
            ? "rgb(var(--accent))"
            : conditional
              ? "rgb(var(--accent) / 0.7)"
              : "rgb(var(--text-muted) / 0.5)",
        strokeWidth: selectedEdgeId === e.id ? 2.5 : conditional ? 2 : 1.5,
        strokeDasharray: conditional ? undefined : "4 4",
      },
    };
  });

  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(layoutNodes);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState(layoutEdges);

  useEffect(() => {
    setRfNodes(layoutNodes);
    setRfEdges(layoutEdges);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, selectedNodeId, selectedEdgeId]);

  const handleConnect = (c: Connection) => {
    if (!c.source || !c.target) return;
    if (!connectionAllowed(nodes, edges, c.source, c.target)) return;
    onConnect(c.source, c.target);
  };

  // Empty-canvas guidance overlay (Phase L1 audit fix): a brand-new
  // pipeline starts with zero nodes — the previous empty canvas looked
  // broken. A non-interactive hint pinned to the centre tells the user
  // exactly what to do next, hides itself the moment the first node
  // lands. ``pointer-events-none`` so it never intercepts drag-drop.
  const empty = nodes.length === 0;
  return (
    <div
      className="relative h-full w-full bg-bg"
      // Drag-and-drop palette → canvas (2026-05-26 user request). The
      // dataTransfer carries the operator id under our custom MIME; ignore
      // any drag that doesn't (file drops, text selections, …).
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes(PALETTE_DND_MIME)) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }
      }}
      onDrop={(e) => {
        const operatorId = e.dataTransfer.getData(PALETTE_DND_MIME);
        if (!operatorId || !onDropOperator) return;
        e.preventDefault();
        // Convert pointer pixels → flow coordinates so the node lands
        // exactly under the cursor regardless of zoom / pan.
        const position = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
        onDropOperator(operatorId, position);
      }}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={handleConnect}
        // Bulk delete: when React Flow fires onNodesDelete for a
        // multi-select, route through the single-commit ``onRemoveNodes``
        // so the undo stack only grows by one. Fall back to per-node
        // removal if the editor didn't wire the bulk callback.
        onNodesDelete={(deleted) => {
          const ids = deleted.map((n) => n.id);
          if (onRemoveNodes) onRemoveNodes(ids);
          else ids.forEach((id) => onRemoveNode(id));
        }}
        onEdgesDelete={(deleted) => deleted.forEach((e) => onRemoveEdge(e.id))}
        onNodeDragStop={(_e, node) => onMoveNode(node.id, node.position)}
        onNodeClick={(_e, node) => onSelectNode(node.id)}
        onEdgeClick={(_e, edge) => onSelectEdge(edge.id)}
        // Cmd/Ctrl held: tells React Flow to treat clicks as additive.
        // Default in current xyflow is already Meta on Mac, but pinning
        // both keys makes the behaviour explicit + portable.
        multiSelectionKeyCode={["Meta", "Control"]}
        // Shift-drag opens a marquee selection box — classic
        // canvas-app pattern, both personas expect it.
        selectionKeyCode="Shift"
        onSelectionChange={(sel) => {
          onSelectionChange?.({
            nodeIds: sel.nodes.map((n) => n.id),
            edgeIds: sel.edges.map((e) => e.id),
          });
        }}
        // Right-click handlers: React Flow gives us the event + the
        // node/edge; we forward to the caller with the screen coords +
        // (for the pane) the flow position so the menu's "Add here" works.
        onPaneContextMenu={(e) => {
          if (!onPaneContextMenu) return;
          e.preventDefault();
          const mouse = e as MouseEvent;
          const flow = rf.screenToFlowPosition({ x: mouse.clientX, y: mouse.clientY });
          onPaneContextMenu({ clientX: mouse.clientX, clientY: mouse.clientY }, flow);
        }}
        onNodeContextMenu={(e, node) => {
          if (!onNodeContextMenu) return;
          e.preventDefault();
          onNodeContextMenu({ clientX: e.clientX, clientY: e.clientY }, node.id);
        }}
        onEdgeContextMenu={(e, edge) => {
          if (!onEdgeContextMenu) return;
          e.preventDefault();
          onEdgeContextMenu({ clientX: e.clientX, clientY: e.clientY }, edge.id);
        }}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.25 }}
        proOptions={{ hideAttribution: true }}
        onPaneClick={() => onDeselect?.()}
      >
        {/* Two-layer 'graph paper' grid (2026-05-26 user request — '노드
            있는 부분에 격자배경 넣어줘'). Fine 20 px cells give a sense of
            snap-distance during drag; the coarser 100 px overlay keeps the
            canvas from looking too busy. Both layers use the same subtle
            border token so theme switching just works. */}
        <Background
          id="grid-fine"
          variant={BackgroundVariant.Lines}
          gap={20}
          lineWidth={1}
          color="rgb(var(--border-subtle) / 0.35)"
        />
        <Background
          id="grid-coarse"
          variant={BackgroundVariant.Lines}
          gap={100}
          lineWidth={1}
          color="rgb(var(--border-subtle) / 0.65)"
        />
        <MiniMap
          zoomable
          pannable
          nodeColor={() => "rgb(var(--bg-elevated))"}
          maskColor="rgb(var(--bg-base) / 0.6)"
          style={{
            backgroundColor: "rgb(var(--bg-surface))",
            border: "1px solid rgb(var(--border-subtle))",
            borderRadius: 8,
          }}
        />
        <Controls
          showInteractive={false}
          className="!rounded-md !border !border-border-subtle !bg-elevated"
        />
      </ReactFlow>
      {empty ? (
        <div
          className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 text-center"
          aria-hidden
        >
          <div className="rounded-full border border-border-subtle bg-elevated/90 px-3 py-1 text-xs uppercase tracking-widest text-text-muted shadow-sm">
            Empty canvas
          </div>
          <div className="max-w-md text-sm text-text-secondary">
            Drag a <strong className="text-text">Source</strong> from the
            left palette to start. Add a <strong className="text-text">Sink</strong>{" "}
            and connect them by dragging from one node's right edge to the
            next.
          </div>
        </div>
      ) : null}
    </div>
  );
}
