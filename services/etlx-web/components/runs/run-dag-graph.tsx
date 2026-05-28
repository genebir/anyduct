"use client";

import { useMemo } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { cn } from "@/lib/cn";
import type { NodeRunEntry, RunStatus } from "@/lib/api";

/**
 * Live DAG progress view for a ``node_level`` run (ADR-0041 H3c).
 *
 * BFS-depth layered layout: roots (no deps) at x=0, downstream pushed right.
 * Status drives the per-node border/background; ``running``/``pending`` edges
 * animate so the user sees the wave move through the graph as polling
 * refreshes the ``node_runs`` list (handled by the parent — this component is
 * a pure render of the snapshot it's given).
 */

const COL_GAP = 240;
const ROW_GAP = 100;
const NODE_W = 200;

type StatusKey = RunStatus | "pending";

const CARD_CLASSES: Record<StatusKey, string> = {
  pending: "border-border-subtle bg-overlay",
  running: "border-info bg-info/10",
  succeeded: "border-success bg-success/10",
  failed: "border-error bg-error/10",
  cancelled: "border-border-subtle bg-overlay/60 opacity-70",
};

const DOT_CLASSES: Record<StatusKey, string> = {
  pending: "bg-text-muted",
  running: "bg-info animate-pulse",
  succeeded: "bg-success",
  failed: "bg-error",
  cancelled: "bg-text-muted",
};

const STATUS_TEXT: Record<StatusKey, string> = {
  pending: "text-text-muted",
  running: "text-info",
  succeeded: "text-success",
  failed: "text-error",
  cancelled: "text-text-muted",
};

/** Render a node-run's duration as ``D 3.2s`` (succeeded/failed/cancelled)
 *  or ``D 1.4s+`` (still running — shows elapsed since started_at, the
 *  trailing ``+`` flags that it's not the final number).
 *
 *  Returns ``null`` for nodes that haven't started yet (no ts to compute
 *  against) so the card stays compact. Bigger durations switch to
 *  ``1m 12s`` / ``2h 3m`` ranges so the figure fits the 200 px card
 *  width without truncation. Phase N (2026-05-28). */
function formatNodeDuration(n: NodeRunEntry): string | null {
  if (!n.started_at) return null;
  const start = Date.parse(n.started_at);
  const end = n.finished_at ? Date.parse(n.finished_at) : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  const ms = end - start;
  const live = !n.finished_at;
  const txt = humanDuration(ms);
  return live ? `D ${txt}+` : `D ${txt}`;
}

function humanDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds - m * 60);
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const mm = m - h * 60;
  return mm > 0 ? `${h}h ${mm}m` : `${h}h`;
}

function bfsDepth(nodes: NodeRunEntry[]): Map<string, number> {
  const byId = new Map(nodes.map((n) => [n.node_id, n]));
  const depth = new Map<string, number>();
  const visiting = new Set<string>(); // cycle guard

  function compute(id: string): number {
    const cached = depth.get(id);
    if (cached !== undefined) return cached;
    if (visiting.has(id)) return 0; // cycle (shouldn't happen with valid DAG)
    visiting.add(id);
    const n = byId.get(id);
    let d = 0;
    if (n && n.depends_on.length > 0) {
      d = Math.max(...n.depends_on.map((p) => compute(p) + 1));
    }
    visiting.delete(id);
    depth.set(id, d);
    return d;
  }

  for (const n of nodes) compute(n.node_id);
  return depth;
}

function nodeCard(n: NodeRunEntry, selected: boolean): React.ReactNode {
  const showCounters = n.records_read > 0 || n.records_written > 0;
  const duration = formatNodeDuration(n);
  return (
    <div
      className={cn(
        "rounded-md border px-2 py-1.5 text-left transition-shadow",
        CARD_CLASSES[n.status],
        // Selected-for-filter visual — accent ring matches the chip in
        // the log panel header so the user sees the link between the
        // two surfaces (Phase M, 2026-05-26).
        selected && "ring-2 ring-accent ring-offset-1 ring-offset-bg",
        // Failed nodes get a thicker error border + glow so they pop
        // against the run DAG without the operator having to scan
        // labels (Phase N, 2026-05-28).
        n.status === "failed" && "shadow-[0_0_0_2px_rgb(var(--error)/0.35)]",
      )}
    >
      <div className="flex items-center gap-1.5">
        <span
          className={cn("h-1.5 w-1.5 shrink-0 rounded-full", DOT_CLASSES[n.status])}
          aria-hidden
        />
        <code className="truncate font-mono text-[11px] text-text" title={n.node_id}>
          {n.node_id}
        </code>
      </div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wider text-text-muted">{n.kind}</span>
        <span className={cn("text-[10px] font-medium", STATUS_TEXT[n.status])}>{n.status}</span>
      </div>
      {showCounters || duration ? (
        <div className="mt-0.5 text-[10px] text-text-secondary">
          {n.records_read > 0 ? `R ${n.records_read}` : null}
          {n.records_read > 0 && n.records_written > 0 ? " · " : null}
          {n.records_written > 0 ? `W ${n.records_written}` : null}
          {(n.records_read > 0 || n.records_written > 0) && duration ? " · " : null}
          {/* Duration display (Phase N, 2026-05-28): turns the silent
              "this node took how long?" question into one glance. For
              still-running nodes we show the elapsed time so the
              operator can see if a node is unexpectedly slow without
              opening the logs. */}
          {duration ? (
            <span className={cn(n.status === "running" && "text-info")}>
              {duration}
            </span>
          ) : null}
        </div>
      ) : null}
      {n.error_class ? (
        <div className="mt-0.5 truncate text-[10px] text-error" title={n.error_message ?? ""}>
          {n.error_class}
        </div>
      ) : null}
    </div>
  );
}

export function RunDagGraph({
  nodes,
  selectedNodeId,
  onSelectNode,
}: {
  nodes: NodeRunEntry[];
  /** Highlight one node card (matches the active log-panel filter). */
  selectedNodeId?: string | null;
  /** Clicking a node card invokes this with the node id (Phase M,
   *  2026-05-26). Parent typically toggles the log filter to that node. */
  onSelectNode?: (nodeId: string) => void;
}) {
  const { rfNodes, rfEdges } = useMemo(() => {
    const depths = bfsDepth(nodes);
    // Group by depth so we can vertically center each column.
    const cols = new Map<number, NodeRunEntry[]>();
    for (const n of nodes) {
      const d = depths.get(n.node_id) ?? 0;
      const list = cols.get(d) ?? [];
      list.push(n);
      cols.set(d, list);
    }
    const positions = new Map<string, { x: number; y: number }>();
    for (const [d, items] of cols) {
      const offset = ((items.length - 1) * ROW_GAP) / 2;
      items.forEach((n, i) => {
        positions.set(n.node_id, { x: d * COL_GAP, y: i * ROW_GAP - offset });
      });
    }
    const rfNodes: Node[] = nodes.map((n) => {
      const p = positions.get(n.node_id) ?? { x: 0, y: 0 };
      return {
        id: n.node_id,
        position: p,
        data: { label: nodeCard(n, selectedNodeId === n.node_id) },
        // Render our own card; clear the default xyflow node chrome.
        style: {
          width: NODE_W,
          padding: 0,
          background: "transparent",
          border: "none",
          // Pointer cursor only when a click handler is wired so the
          // affordance honestly reflects "this does something".
          cursor: onSelectNode ? "pointer" : "default",
        },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
      };
    });
    const rfEdges: Edge[] = [];
    for (const n of nodes) {
      for (const up of n.depends_on) {
        // Animate edges feeding a still-pending/running node so the user
        // sees the wave move through the graph as data refreshes.
        const animated = n.status === "pending" || n.status === "running";
        rfEdges.push({
          id: `${up}->${n.node_id}`,
          source: up,
          target: n.node_id,
          animated,
          style: { stroke: "rgb(var(--border-default))" },
        });
      }
    }
    return { rfNodes, rfEdges };
  }, [nodes, selectedNodeId, onSelectNode]);

  return (
    <div className="h-80 w-full overflow-hidden rounded-md border border-border-subtle bg-surface">
      <ReactFlowProvider>
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          fitView
          nodesDraggable={false}
          nodesConnectable={false}
          // ``elementsSelectable`` must be true for onNodeClick to fire;
          // we still keep the node visually unselected via the custom
          // card so the only "selection" the user sees is the accent
          // ring we paint ourselves.
          elementsSelectable={Boolean(onSelectNode)}
          onNodeClick={
            onSelectNode ? (_e, node) => onSelectNode(node.id) : undefined
          }
          panOnDrag
          zoomOnScroll
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={16} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  );
}
