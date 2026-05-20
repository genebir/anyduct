"use client";

import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect, useMemo } from "react";
import { PipelineNode, type PipelineNodeData } from "./pipeline-node";
import { findOperator } from "@/lib/operators";
import type { BuilderNode } from "@/lib/pipeline-config";
import { reorderNodes } from "@/lib/pipeline-config";

const NODE_WIDTH = 260;
const NODE_GAP = 60;
const NODE_VGAP = 150;
const NODE_TYPES = { pipelineNode: PipelineNode };

export function BuilderCanvas({
  nodes,
  selectedId,
  onSelect,
  onRemove,
  onDeselect,
  onReorderTransforms,
}: {
  nodes: BuilderNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
  onDeselect?: () => void;
  /** Called with the new transform id order after a drag settles. */
  onReorderTransforms?: (orderedTransformIds: string[]) => void;
}) {
  // Slot layout derived from the canonical (kind-sorted) order. Transforms
  // are draggable; source/sink are pinned. After a drag we recompute the
  // transform order by x and hand it back so the parent owns the state.
  const { layoutNodes, rfEdges } = useMemo(() => {
    const ordered = reorderNodes(nodes);
    // The "spine" = source → transforms laid out left-to-right; terminals
    // (sinks + call-pipeline nodes) fan out from the spine tail, stacked
    // vertically when there's more than one (fan-out ADR-0026 / call ADR-0029).
    const isTerminal = (n: BuilderNode) => {
      const k = findOperator(n.operatorId)?.kind;
      return k === "sink" || k === "call";
    };
    const spine = ordered.filter((n) => !isTerminal(n));
    const sinks = ordered.filter(isTerminal);
    const multiSink = sinks.length > 1;
    const sinkCol = spine.length;

    const layoutNodes: Node<PipelineNodeData>[] = [];
    spine.forEach((n, i) => {
      const op = findOperator(n.operatorId);
      const isTransform = op?.kind === "transform";
      layoutNodes.push({
        id: n.id,
        type: "pipelineNode",
        position: { x: i * (NODE_WIDTH + NODE_GAP), y: 0 },
        draggable: isTransform,
        data: {
          operatorId: n.operatorId,
          values: n.data,
          selected: selectedId === n.id,
          onSelect,
          onRemove,
          canRemove: isTransform,
        },
      });
    });
    const sinkKindCount = sinks.filter(
      (n) => findOperator(n.operatorId)?.kind === "sink",
    ).length;
    sinks.forEach((n, i) => {
      // Center the stack vertically around the spine row.
      const y = multiSink ? (i - (sinks.length - 1) / 2) * NODE_VGAP : 0;
      const isCall = findOperator(n.operatorId)?.kind === "call";
      layoutNodes.push({
        id: n.id,
        type: "pipelineNode",
        position: { x: sinkCol * (NODE_WIDTH + NODE_GAP), y },
        draggable: false,
        data: {
          operatorId: n.operatorId,
          values: n.data,
          selected: selectedId === n.id,
          onSelect,
          onRemove,
          // Call nodes are always removable (optional); a sink only when there
          // is more than one (a pipeline must keep ≥1 sink).
          canRemove: isCall || sinkKindCount > 1,
        },
      });
    });

    const rfEdges: Edge[] = [];
    const edgeStyle = { stroke: "rgb(var(--accent))", strokeWidth: 1.5 };
    for (let i = 0; i < spine.length - 1; i++) {
      rfEdges.push({
        id: `${spine[i].id}->${spine[i + 1].id}`,
        source: spine[i].id,
        target: spine[i + 1].id,
        animated: true,
        style: edgeStyle,
      });
    }
    const tail = spine[spine.length - 1];
    if (tail) {
      for (const s of sinks) {
        rfEdges.push({
          id: `${tail.id}->${s.id}`,
          source: tail.id,
          target: s.id,
          animated: true,
          style: edgeStyle,
        });
      }
    }
    return { layoutNodes, rfEdges };
  }, [nodes, selectedId, onSelect, onRemove]);

  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(layoutNodes);

  // Re-sync to the slot layout whenever the order/selection/data changes
  // (incl. right after a reorder snaps the dragged node into its new slot).
  useEffect(() => {
    setRfNodes(layoutNodes);
  }, [layoutNodes, setRfNodes]);

  const onNodeDragStop: NodeMouseHandler = () => {
    if (!onReorderTransforms) return;
    const ordered = reorderNodes(nodes);
    const transformIds = new Set(
      ordered
        .filter((n) => findOperator(n.operatorId)?.kind === "transform")
        .map((n) => n.id),
    );
    const newOrder = [...rfNodes]
      .filter((n) => transformIds.has(n.id))
      .sort((a, b) => a.position.x - b.position.x)
      .map((n) => n.id);
    onReorderTransforms(newOrder);
  };

  return (
    <ReactFlowProvider>
      <div className="h-full w-full bg-bg">
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          onNodesChange={onNodesChange}
          onNodeDragStop={onNodeDragStop}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={{ padding: 0.25 }}
          nodesConnectable={false}
          edgesFocusable={false}
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
