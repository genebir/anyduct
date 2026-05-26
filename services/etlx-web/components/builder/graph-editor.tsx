"use client";

import { useCallback, useState } from "react";
import { Trash2Icon } from "lucide-react";
import { Palette } from "@/components/builder/palette";
import { PropertiesPanel, FilterEditor } from "@/components/builder/properties-panel";
import { GraphCanvas } from "@/components/builder/graph-canvas";
import { useLocale } from "@/components/providers/locale-provider";
import type { ConnectionSummary } from "@/lib/api";
import {
  makeGraphNode,
  nextEdgeId,
  type GraphBuilderEdge,
  type GraphBuilderState,
} from "@/lib/pipeline-config";

/**
 * Free-form dataflow graph editor (ADR-0030). The user drops operator nodes,
 * draws edges, and sets an optional branch condition (`when`) on each edge.
 * Owns selection; all state edits flow up through `onChange`.
 */
export function GraphEditor({
  state,
  connections,
  mode = "batch",
  onChange,
  settingsPanel,
  dryRunPanel,
  workspaceId,
}: {
  state: GraphBuilderState;
  connections: ConnectionSummary[];
  mode?: "batch" | "stream";
  onChange: (next: GraphBuilderState) => void;
  /** Rendered in the right side when no node / edge is selected (graph-only
   *  mode, 2026-05-26). Callers usually pass a ``PipelineSettingsPanel``
   *  here — retry/dlq/variables/downstream triggers all live there now. */
  settingsPanel?: React.ReactNode;
  /** Rendered below the canvas — typically the dry-run result panel. */
  dryRunPanel?: React.ReactNode;
  /** Workspace id forwarded to the properties panel (column introspection). */
  workspaceId?: string;
}) {
  const { t } = useLocale();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);

  const selectNode = useCallback((id: string) => {
    setSelectedNodeId(id);
    setSelectedEdgeId(null);
  }, []);
  const selectEdge = useCallback((id: string) => {
    setSelectedEdgeId(id);
    setSelectedNodeId(null);
  }, []);

  const addOperator = useCallback(
    (operatorId: string) => {
      const node = makeGraphNode(operatorId, {
        x: 80 + (state.nodes.length % 4) * 40,
        y: 260 + (state.nodes.length % 6) * 30,
      });
      onChange({ ...state, nodes: [...state.nodes, node] });
      setSelectedNodeId(node.id);
      setSelectedEdgeId(null);
    },
    [state, onChange],
  );

  const removeNode = useCallback(
    (id: string) => {
      onChange({
        nodes: state.nodes.filter((n) => n.id !== id),
        edges: state.edges.filter((e) => e.source !== id && e.target !== id),
      });
      setSelectedNodeId((cur) => (cur === id ? null : cur));
    },
    [state, onChange],
  );

  const updateNode = useCallback(
    (id: string, data: Record<string, unknown>) => {
      onChange({
        ...state,
        nodes: state.nodes.map((n) => (n.id === id ? { ...n, data } : n)),
      });
    },
    [state, onChange],
  );

  const moveNode = useCallback(
    (id: string, position: { x: number; y: number }) => {
      onChange({
        ...state,
        nodes: state.nodes.map((n) => (n.id === id ? { ...n, position } : n)),
      });
    },
    [state, onChange],
  );

  const connect = useCallback(
    (source: string, target: string) => {
      const edge: GraphBuilderEdge = { id: nextEdgeId(), source, target };
      onChange({ ...state, edges: [...state.edges, edge] });
    },
    [state, onChange],
  );

  const removeEdge = useCallback(
    (id: string) => {
      onChange({ ...state, edges: state.edges.filter((e) => e.id !== id) });
      setSelectedEdgeId((cur) => (cur === id ? null : cur));
    },
    [state, onChange],
  );

  const setEdgeWhen = useCallback(
    (id: string, when: string | undefined) => {
      onChange({
        ...state,
        edges: state.edges.map((e) => (e.id === id ? { ...e, when } : e)),
      });
    },
    [state, onChange],
  );

  const selectedNode = state.nodes.find((n) => n.id === selectedNodeId) ?? null;
  const selectedEdge = state.edges.find((e) => e.id === selectedEdgeId) ?? null;

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <Palette onAdd={addOperator} mode={mode} variant="graph" />
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1">
          <GraphCanvas
            nodes={state.nodes}
            edges={state.edges}
            selectedNodeId={selectedNodeId}
            selectedEdgeId={selectedEdgeId}
            onSelectNode={selectNode}
            onSelectEdge={selectEdge}
            onRemoveNode={removeNode}
            onConnect={connect}
            onRemoveEdge={removeEdge}
            onMoveNode={moveNode}
            onDeselect={() => {
              setSelectedNodeId(null);
              setSelectedEdgeId(null);
            }}
          />
        </div>
        {dryRunPanel}
      </div>
      {selectedEdge ? (
        <aside className="flex w-80 shrink-0 flex-col gap-4 overflow-y-auto border-l border-border-subtle bg-surface p-4">
          <header className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-text">{t("graph.branchCondition")}</h2>
            <button
              type="button"
              onClick={() => removeEdge(selectedEdge.id)}
              aria-label={t("graph.removeEdge")}
              className="rounded-sm p-1 text-text-muted hover:text-error"
            >
              <Trash2Icon size={14} />
            </button>
          </header>
          <p className="text-[11px] text-text-muted">{t("graph.branchConditionHelp")}</p>
          <FilterEditor
            value={selectedEdge.when ?? ""}
            onChange={(v) => setEdgeWhen(selectedEdge.id, (v as string) || undefined)}
            t={t}
          />
        </aside>
      ) : selectedNode ? (
        <PropertiesPanel
          node={{ id: selectedNode.id, operatorId: selectedNode.operatorId, data: selectedNode.data }}
          connections={connections}
          workspaceId={workspaceId}
          onChange={updateNode}
          onClose={() => setSelectedNodeId(null)}
        />
      ) : settingsPanel ? (
        settingsPanel
      ) : (
        <aside className="flex w-80 shrink-0 flex-col border-l border-border-subtle bg-surface px-4 py-6 text-sm text-text-muted">
          {t("graph.hint")}
        </aside>
      )}
    </div>
  );
}
