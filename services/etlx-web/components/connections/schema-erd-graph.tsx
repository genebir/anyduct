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
import { KeyIcon } from "lucide-react";
import { buildErdModel, type ErdColumn, type RawTable } from "@/lib/erd";

const NODE_W = 240;
const COL_GAP = 320;
const ROW_GAP = 260;

function tableNode(table: string, columns: ErdColumn[]): React.ReactNode {
  return (
    <div className="w-full text-left">
      <div className="truncate rounded-t-[7px] border-b border-border-subtle bg-overlay px-2.5 py-1.5 font-mono text-[11px] font-semibold text-text" title={table}>
        {table}
      </div>
      <div className="max-h-[200px] overflow-auto">
        {columns.length === 0 ? (
          <div className="px-2.5 py-1.5 text-[10px] italic text-text-muted">(no columns)</div>
        ) : (
          columns.map((c) => (
            <div
              key={c.name}
              className="flex items-center gap-1.5 border-b border-border-subtle/40 px-2.5 py-1 last:border-0"
            >
              {c.isKey ? (
                <KeyIcon size={10} className="shrink-0 text-warning" aria-label="key" />
              ) : (
                <span className="inline-block w-[10px] shrink-0" />
              )}
              <span
                className={`flex-1 truncate font-mono text-[11px] ${c.isRef ? "text-accent" : "text-text"}`}
                title={`${c.name}: ${c.type}`}
              >
                {c.name}
                {c.isRef ? " →" : ""}
              </span>
              <span className="shrink-0 truncate font-mono text-[10px] text-text-muted" title={c.type}>
                {c.type}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

/**
 * Read-only ERD of a connection's schema. Tables are entity boxes listing
 * their columns; edges are FK relationships **inferred by ``<x>_id``
 * naming convention** (connectors don't expose real FKs yet). Reuses
 * @xyflow/react. Nodes are draggable so the operator can rearrange.
 */
export function SchemaErdGraph({ tables }: { tables: RawTable[] }) {
  const { nodes, edges } = useMemo(() => {
    const model = buildErdModel(tables);
    const perRow = Math.max(1, Math.ceil(Math.sqrt(model.entities.length)));
    const nodes: Node[] = model.entities.map((e, i) => ({
      id: e.table,
      position: { x: (i % perRow) * COL_GAP, y: Math.floor(i / perRow) * ROW_GAP },
      data: { label: tableNode(e.table, e.columns) },
      style: {
        width: NODE_W,
        padding: 0,
        borderRadius: 8,
        border: "1px solid rgb(var(--border-subtle))",
        background: "rgb(var(--bg-elevated))",
        color: "rgb(var(--text))",
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      connectable: false,
    }));
    const edgeStyle = { stroke: "rgb(var(--accent))", strokeWidth: 1.5 };
    const edges: Edge[] = model.relations.map((r) => ({
      id: `${r.from}.${r.column}->${r.to}`,
      source: r.from,
      target: r.to,
      label: r.column,
      animated: true,
      style: edgeStyle,
      labelStyle: { fontSize: 10, fill: "rgb(var(--text-muted))" },
    }));
    return { nodes, edges };
  }, [tables]);

  return (
    <ReactFlowProvider>
      <div className="h-[600px] w-full rounded-md border border-border-subtle bg-bg">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          nodesConnectable={false}
          edgesFocusable={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgb(var(--border-subtle) / 0.6)" />
          <Controls showInteractive={false} className="!rounded-md !border !border-border-subtle !bg-elevated" />
        </ReactFlow>
      </div>
    </ReactFlowProvider>
  );
}
