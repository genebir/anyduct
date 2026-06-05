"use client";

import { useMemo } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { AssetRef } from "@/lib/api";

interface LineageAsset {
  id: string;
  asset_key: string;
  kind?: string | null;
}

const COL_GAP = 300;
const ROW_GAP = 90;
const NODE_W = 240;

function label(a: LineageAsset, isCurrent: boolean): React.ReactNode {
  return (
    <div className="px-2 py-1.5 text-left">
      <div className="truncate font-mono text-[11px] text-text" title={a.asset_key}>
        {a.asset_key}
      </div>
      <div className="mt-0.5 text-[10px] uppercase tracking-wider text-text-muted">
        {isCurrent ? "this asset" : (a.kind ?? "asset")}
      </div>
    </div>
  );
}

function nodeStyle(isCurrent: boolean): React.CSSProperties {
  return {
    width: NODE_W,
    padding: 0,
    borderRadius: 8,
    border: isCurrent ? "2px solid rgb(var(--accent))" : "1px solid rgb(var(--border-subtle))",
    background: "rgb(var(--bg-elevated))",
    color: "rgb(var(--text))",
    boxShadow: isCurrent ? "0 0 0 3px rgb(var(--accent) / 0.15)" : undefined,
  };
}

function column(items: LineageAsset[], x: number, isCurrent = false): Node[] {
  const offset = ((items.length - 1) * ROW_GAP) / 2;
  return items.map((a, i) => ({
    id: a.id,
    position: { x, y: i * ROW_GAP - offset },
    data: { label: label(a, isCurrent) },
    style: nodeStyle(isCurrent),
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    draggable: false,
    connectable: false,
  }));
}

/**
 * Read-only lineage view: upstream assets feed the current asset (centre),
 * which feeds downstream assets. Reuses @xyflow/react (the pipeline builder's
 * graph lib). Clicking a neighbour navigates to it.
 */
export function LineageGraph({
  current,
  upstream,
  downstream,
  onSelect,
}: {
  current: LineageAsset;
  upstream: AssetRef[];
  downstream: AssetRef[];
  onSelect?: (assetId: string) => void;
}) {
  const { nodes, edges } = useMemo(() => {
    const nodes: Node[] = [
      ...column(upstream, 0),
      ...column([current], COL_GAP, true),
      ...column(downstream, COL_GAP * 2),
    ];
    const edgeStyle = { stroke: "rgb(var(--accent))", strokeWidth: 1.5 };
    const edges: Edge[] = [
      ...upstream.map((u) => ({
        id: `${u.id}->${current.id}`,
        source: u.id,
        target: current.id,
        animated: true,
        style: edgeStyle,
      })),
      ...downstream.map((d) => ({
        id: `${current.id}->${d.id}`,
        source: current.id,
        target: d.id,
        animated: true,
        style: edgeStyle,
      })),
    ];
    return { nodes, edges };
  }, [current, upstream, downstream]);

  const onNodeClick: NodeMouseHandler = (_e, node) => {
    if (node.id !== current.id) onSelect?.(node.id);
  };

  return (
    <ReactFlowProvider>
      <div className="h-[420px] w-full rounded-md border border-border-subtle bg-bg">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodeClick={onNodeClick}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          nodesConnectable={false}
          nodesDraggable={false}
          edgesFocusable={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgb(var(--border-subtle) / 0.6)" />
          <Controls showInteractive={false} className="!rounded-md !border !border-border-subtle !bg-elevated" />
          <MiniMap
            pannable
            zoomable
            className="!rounded-md !border !border-border-subtle !bg-elevated"
            maskColor="rgb(var(--bg) / 0.6)"
            nodeColor="rgb(var(--accent) / 0.45)"
            nodeStrokeColor="rgb(var(--border-subtle))"
          />
        </ReactFlow>
      </div>
    </ReactFlowProvider>
  );
}
