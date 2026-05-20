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
  type Connection,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect } from "react";
import { PipelineNode, type PipelineNodeData } from "./pipeline-node";
import { findOperator } from "@/lib/operators";
import type { GraphBuilderEdge, GraphBuilderNode } from "@/lib/pipeline-config";

const NODE_TYPES = { pipelineNode: PipelineNode };

/** Adding source→target keeps the source-rooted tree (ADR-0030 v1): no edge
 *  into the source, ≤1 incoming edge per node, no cycles. */
function connectionAllowed(
  nodes: GraphBuilderNode[],
  edges: GraphBuilderEdge[],
  source: string,
  target: string,
): boolean {
  if (source === target) return false;
  const tgt = nodes.find((n) => n.id === target);
  if (findOperator(tgt?.operatorId ?? "")?.kind === "source") return false; // no edge into a source
  if (edges.some((e) => e.target === target)) return false; // tree: one incoming edge
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

export function GraphCanvas({
  nodes,
  edges,
  selectedNodeId,
  selectedEdgeId,
  onSelectNode,
  onSelectEdge,
  onRemoveNode,
  onConnect,
  onRemoveEdge,
  onMoveNode,
  onDeselect,
}: {
  nodes: GraphBuilderNode[];
  edges: GraphBuilderEdge[];
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onRemoveNode: (id: string) => void;
  onConnect: (source: string, target: string) => void;
  onRemoveEdge: (id: string) => void;
  onMoveNode: (id: string, pos: { x: number; y: number }) => void;
  onDeselect?: () => void;
}) {
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

  const layoutEdges: Edge[] = edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    animated: true,
    label: e.when ? `if ${e.when}` : undefined,
    labelStyle: { fill: "rgb(var(--accent))", fontSize: 10 },
    labelBgStyle: { fill: "rgb(var(--bg-surface))" },
    selected: selectedEdgeId === e.id,
    style: {
      stroke: selectedEdgeId === e.id ? "rgb(var(--accent))" : "rgb(var(--accent) / 0.6)",
      strokeWidth: selectedEdgeId === e.id ? 2.5 : 1.5,
    },
  }));

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

  return (
    <ReactFlowProvider>
      <div className="h-full w-full bg-bg">
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={handleConnect}
          onEdgesDelete={(deleted) => deleted.forEach((e) => onRemoveEdge(e.id))}
          onNodeDragStop={(_e, node) => onMoveNode(node.id, node.position)}
          onNodeClick={(_e, node) => onSelectNode(node.id)}
          onEdgeClick={(_e, edge) => onSelectEdge(edge.id)}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={{ padding: 0.25 }}
          proOptions={{ hideAttribution: true }}
          onPaneClick={() => onDeselect?.()}
        >
          <Background
            variant={BackgroundVariant.Dots}
            gap={20}
            size={1}
            color="rgb(var(--border-subtle) / 0.6)"
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
      </div>
    </ReactFlowProvider>
  );
}
