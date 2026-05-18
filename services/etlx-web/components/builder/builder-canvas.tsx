"use client";

import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";
import { PipelineNode, type PipelineNodeData } from "./pipeline-node";
import { findOperator } from "@/lib/operators";
import type { BuilderNode } from "@/lib/pipeline-config";
import { reorderNodes } from "@/lib/pipeline-config";

const NODE_WIDTH = 260;
const NODE_GAP = 60;
const NODE_TYPES = { pipelineNode: PipelineNode };

export function BuilderCanvas({
  nodes,
  selectedId,
  onSelect,
  onRemove,
  onDeselect,
}: {
  nodes: BuilderNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
  onDeselect?: () => void;
}) {
  const { rfNodes, rfEdges } = useMemo(() => {
    const ordered = reorderNodes(nodes);
    const rfNodes: Node<PipelineNodeData>[] = ordered.map((n, i) => {
      const op = findOperator(n.operatorId);
      return {
        id: n.id,
        type: "pipelineNode",
        position: { x: i * (NODE_WIDTH + NODE_GAP), y: 0 },
        data: {
          operatorId: n.operatorId,
          values: n.data,
          selected: selectedId === n.id,
          onSelect,
          onRemove,
          canRemove: op?.kind === "transform",
        },
      };
    });
    const rfEdges: Edge[] = [];
    for (let i = 0; i < ordered.length - 1; i++) {
      rfEdges.push({
        id: `${ordered[i].id}->${ordered[i + 1].id}`,
        source: ordered[i].id,
        target: ordered[i + 1].id,
        animated: true,
        style: { stroke: "rgb(var(--accent))", strokeWidth: 1.5 },
      });
    }
    return { rfNodes, rfEdges };
  }, [nodes, selectedId, onSelect, onRemove]);

  return (
    <ReactFlowProvider>
      <div className="h-full w-full bg-bg">
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={{ padding: 0.25 }}
          nodesDraggable={false}
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
