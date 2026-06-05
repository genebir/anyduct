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
import { useLocale } from "@/components/providers/locale-provider";
import type { AssetColumnEntry, ColumnUpstreamRef } from "@/lib/api";

const COL_GAP = 320;
const ROW_GAP = 36;
const HEADER_HEIGHT = 28;
const GROUP_GAP = 24; // vertical gap between upstream asset groups
const NODE_W = 240;

type UpstreamGroup = { assetId: string; assetKey: string; columns: string[] };

/** Bucket upstream column refs by their source asset, preserving deterministic
 * order (asset_key, then column name). */
function groupUpstreams(columns: AssetColumnEntry[]): UpstreamGroup[] {
  const byAsset = new Map<string, { assetKey: string; columns: Set<string> }>();
  for (const col of columns) {
    for (const up of col.upstreams) {
      const entry = byAsset.get(up.asset_id);
      if (entry) entry.columns.add(up.column);
      else byAsset.set(up.asset_id, { assetKey: up.asset_key, columns: new Set([up.column]) });
    }
  }
  return Array.from(byAsset.entries())
    .map(([assetId, v]) => ({
      assetId,
      assetKey: v.assetKey,
      columns: Array.from(v.columns).sort(),
    }))
    .sort((a, b) => a.assetKey.localeCompare(b.assetKey));
}

/** Compose a stable node id for an upstream column so edges can address it. */
function upstreamColumnNodeId(assetId: string, columnName: string): string {
  return `up:${assetId}:${columnName}`;
}

function downstreamColumnNodeId(columnName: string): string {
  return `dn:${columnName}`;
}

function columnNodeLabel(name: string, isCurrent: boolean): React.ReactNode {
  return (
    <div className="px-2 py-1 text-left">
      <div className="truncate font-mono text-[11px] text-text" title={name}>
        {name}
      </div>
      <div className="mt-0.5 text-[9px] uppercase tracking-wider text-text-muted">
        {isCurrent ? "this column" : "upstream"}
      </div>
    </div>
  );
}

function groupHeaderLabel(assetKey: string): React.ReactNode {
  return (
    <div className="px-2 py-1 text-left">
      <div className="truncate text-[10px] uppercase tracking-wider text-text-muted">asset</div>
      <div className="truncate font-mono text-[11px] text-text-secondary" title={assetKey}>
        {assetKey}
      </div>
    </div>
  );
}

function columnNodeStyle(isCurrent: boolean): React.CSSProperties {
  return {
    width: NODE_W,
    padding: 0,
    borderRadius: 6,
    border: isCurrent
      ? "1.5px solid rgb(var(--accent))"
      : "1px solid rgb(var(--border-subtle))",
    background: "rgb(var(--bg-elevated))",
    color: "rgb(var(--text))",
    fontSize: 11,
  };
}

function groupHeaderStyle(): React.CSSProperties {
  return {
    width: NODE_W,
    padding: 0,
    borderRadius: 6,
    border: "1px dashed rgb(var(--border-subtle))",
    background: "transparent",
    color: "rgb(var(--text-muted))",
  };
}

/**
 * Read-only column-level lineage view (ADR-0041 J3).
 *
 * Two-column layout:
 *   - Left:  upstream columns grouped by their source asset.
 *   - Right: the current asset's columns (alphabetical).
 *
 * Edges connect each downstream column to its upstream column(s). Columns
 * with no upstreams (constants / opaque expressions) render as plain right-
 * column nodes with no incoming edge.
 *
 * Caller is responsible for skipping render when `opaque=true` (it shows a
 * banner instead) — this component assumes there are columns to draw.
 */
export function ColumnLineageGraph({
  columns,
  onSelectAsset,
}: {
  columns: AssetColumnEntry[];
  onSelectAsset?: (assetId: string) => void;
}) {
  const { t } = useLocale();
  const { nodes, edges, height } = useMemo(() => {
    const groups = groupUpstreams(columns);

    // --- left column: stacked upstream groups -----------------------------
    const leftNodes: Node[] = [];
    let leftY = 0;
    const leftYByColumn = new Map<string, number>(); // upstream node id → y
    for (const g of groups) {
      // Group header (read-only, click navigates to asset detail).
      const headerId = `header:${g.assetId}`;
      leftNodes.push({
        id: headerId,
        position: { x: 0, y: leftY },
        data: { label: groupHeaderLabel(g.assetKey) },
        style: groupHeaderStyle(),
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        draggable: false,
        connectable: false,
        selectable: false,
      });
      leftY += HEADER_HEIGHT;
      for (const colName of g.columns) {
        const nodeId = upstreamColumnNodeId(g.assetId, colName);
        leftNodes.push({
          id: nodeId,
          position: { x: 0, y: leftY },
          data: { label: columnNodeLabel(colName, false), assetId: g.assetId },
          style: columnNodeStyle(false),
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
          draggable: false,
          connectable: false,
        });
        leftYByColumn.set(nodeId, leftY);
        leftY += ROW_GAP;
      }
      leftY += GROUP_GAP;
    }

    // --- right column: current asset's columns ---------------------------
    const rightColumns = [...columns].sort((a, b) => a.name.localeCompare(b.name));
    const rightNodes: Node[] = rightColumns.map((col, i) => ({
      id: downstreamColumnNodeId(col.name),
      position: { x: COL_GAP, y: i * ROW_GAP },
      data: { label: columnNodeLabel(col.name, true) },
      style: columnNodeStyle(true),
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      draggable: false,
      connectable: false,
    }));

    // --- edges ---------------------------------------------------------------
    const edgeStyle = { stroke: "rgb(var(--accent))", strokeWidth: 1.25, opacity: 0.7 };
    const edges: Edge[] = [];
    for (const col of columns) {
      for (const up of col.upstreams) {
        const src = upstreamColumnNodeId(up.asset_id, up.column);
        const dst = downstreamColumnNodeId(col.name);
        edges.push({
          id: `${src}->${dst}`,
          source: src,
          target: dst,
          style: edgeStyle,
        });
      }
    }

    const usedRight = rightColumns.length * ROW_GAP;
    const usedLeft = leftY;
    return {
      nodes: [...leftNodes, ...rightNodes],
      edges,
      // Reserve enough height for the taller side; cap so the graph stays
      // readable inside the card.
      height: Math.min(560, Math.max(280, Math.max(usedRight, usedLeft) + 40)),
    };
  }, [columns]);

  const onNodeClick: NodeMouseHandler = (_e, node) => {
    if (node.id.startsWith("header:")) {
      const assetId = node.id.slice("header:".length);
      onSelectAsset?.(assetId);
      return;
    }
    const data = node.data as { assetId?: string } | undefined;
    if (data?.assetId) onSelectAsset?.(data.assetId);
  };

  if (columns.length === 0) {
    return (
      <div
        className="flex h-32 items-center justify-center text-sm text-text-muted"
        role="status"
      >
        {t("assets.columnLineageEmpty")}
      </div>
    );
  }

  return (
    <ReactFlowProvider>
      <div
        className="w-full rounded-md border border-border-subtle bg-bg"
        style={{ height }}
        data-testid="column-lineage-graph"
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodeClick={onNodeClick}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          nodesConnectable={false}
          nodesDraggable={false}
          edgesFocusable={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background
            variant={BackgroundVariant.Dots}
            gap={18}
            size={1}
            color="rgb(var(--border-subtle) / 0.5)"
          />
          <Controls
            showInteractive={false}
            className="!rounded-md !border !border-border-subtle !bg-elevated"
          />
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
