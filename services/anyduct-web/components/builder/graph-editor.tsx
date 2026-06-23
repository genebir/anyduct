"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  CopyIcon,
  EditIcon,
  GitBranchIcon,
  Trash2Icon,
  UnlinkIcon,
} from "lucide-react";
import { Palette } from "@/components/builder/palette";
import { PropertiesPanel, FilterEditor } from "@/components/builder/properties-panel";
import { GraphCanvas } from "@/components/builder/graph-canvas";
import { useLocale } from "@/components/providers/locale-provider";
import {
  ContextMenu,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuSubmenu,
  useContextMenu,
} from "@/components/ui/context-menu";
import {
  OPERATOR_KIND_GROUPS,
  getOperatorLabel,
  operatorAllowedForMode,
} from "@/lib/operators";
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
  builderKind = "dataflow",
  onChange,
  settingsPanel,
  dryRunPanel,
  workspaceId,
  focusRequest,
}: {
  state: GraphBuilderState;
  connections: ConnectionSummary[];
  mode?: "batch" | "stream";
  /** "orchestration" (Operator DAG, ADR-0099) swaps the palette to operator
   *  steps; "dataflow" (default) is the source/transform/sink graph. */
  builderKind?: "dataflow" | "orchestration";
  onChange: (next: GraphBuilderState) => void;
  /** Rendered in the right side when no node / edge is selected (graph-only
   *  mode, 2026-05-26). Callers usually pass a ``PipelineSettingsPanel``
   *  here — retry/dlq/variables/downstream triggers all live there now. */
  settingsPanel?: React.ReactNode;
  /** Rendered below the canvas — typically the dry-run result panel. */
  dryRunPanel?: React.ReactNode;
  /** Workspace id forwarded to the properties panel (column introspection). */
  workspaceId?: string;
  /** Imperative "select this node" trigger from a parent (e.g. the
   *  validation banner). The shape ``{nodeId, nonce}`` lets the parent
   *  re-focus the same node twice in a row by bumping ``nonce`` — a
   *  bare nodeId wouldn't fire React's identity check the second time.
   *  Phase L1, 2026-05-26. */
  focusRequest?: { nodeId: string; nonce: number } | null;
}) {
  const { t } = useLocale();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  // Multi-selection mirror (Phase L1, 2026-05-26). React Flow handles
  // the visual selection itself; we shadow it here so global shortcuts
  // (Cmd+D duplicate) know what to act on without a child→parent ref.
  const selectedIdsRef = useRef<{ nodeIds: string[]; edgeIds: string[] }>({
    nodeIds: [],
    edgeIds: [],
  });

  const selectNode = useCallback((id: string) => {
    setSelectedNodeId(id);
    setSelectedEdgeId(null);
  }, []);

  // Honour an external focus request (validation banner click etc.).
  // Re-runs whenever the parent bumps ``nonce`` so the same node can
  // be re-focused after the user has clicked away.
  useEffect(() => {
    if (focusRequest && focusRequest.nodeId) {
      selectNode(focusRequest.nodeId);
    }
  }, [focusRequest, selectNode]);
  const selectEdge = useCallback((id: string) => {
    setSelectedEdgeId(id);
    setSelectedNodeId(null);
  }, []);

  const addOperator = useCallback(
    (operatorId: string, position?: { x: number; y: number }) => {
      // Cascade default placement so click-added nodes don't pile on top
      // of each other. Drop-added nodes pass an explicit ``position`` from
      // the canvas's screenToFlowPosition() so they land under the cursor.
      const pos = position ?? {
        x: 80 + (state.nodes.length % 4) * 40,
        y: 260 + (state.nodes.length % 6) * 30,
      };
      const node = makeGraphNode(operatorId, pos);
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

  /** Single-commit bulk delete used by Delete/Backspace + React Flow's
   *  built-in selection delete. Without this each node-removal would
   *  push its own undo snapshot, blowing the stack on a 10-node bulk
   *  delete (Phase L1 audit). One commit → one Cmd+Z restore. */
  const removeNodes = useCallback(
    (ids: string[]) => {
      if (ids.length === 0) return;
      const set = new Set(ids);
      onChange({
        nodes: state.nodes.filter((n) => !set.has(n.id)),
        edges: state.edges.filter((e) => !set.has(e.source) && !set.has(e.target)),
      });
      setSelectedNodeId((cur) => (cur && set.has(cur) ? null : cur));
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

  const duplicateNode = useCallback(
    (id: string) => {
      const src = state.nodes.find((n) => n.id === id);
      if (!src) return;
      const copy = makeGraphNode(src.operatorId, {
        x: src.position.x + 40,
        y: src.position.y + 40,
      });
      // Carry the source's data so duplicate is genuinely a copy + nudge.
      copy.data = { ...src.data };
      onChange({ ...state, nodes: [...state.nodes, copy] });
      setSelectedNodeId(copy.id);
      setSelectedEdgeId(null);
    },
    [state, onChange],
  );

  /** Duplicate every node in ``ids`` AND any edges that live entirely
   *  inside the selection (so Cmd+D on a 3-node sub-graph genuinely
   *  copies a 3-node sub-graph, not three orphans the user has to
   *  re-wire). New ids are minted via :func:`makeGraphNode`; old→new
   *  id mapping is used to translate the kept edges. Positions are
   *  offset by (40, 40) like the single-node duplicate so the copies
   *  don't sit on top of the originals. Phase L1, 2026-05-26. */
  const duplicateNodes = useCallback(
    (ids: string[]) => {
      if (ids.length === 0) return;
      const idSet = new Set(ids);
      const oldToNew = new Map<string, string>();
      const copies = state.nodes
        .filter((n) => idSet.has(n.id))
        .map((src) => {
          const copy = makeGraphNode(src.operatorId, {
            x: src.position.x + 40,
            y: src.position.y + 40,
          });
          copy.data = { ...src.data };
          oldToNew.set(src.id, copy.id);
          return copy;
        });
      const internalEdges = state.edges
        .filter((e) => idSet.has(e.source) && idSet.has(e.target))
        .map((e) => ({
          id: nextEdgeId(),
          source: oldToNew.get(e.source)!,
          target: oldToNew.get(e.target)!,
          when: e.when,
        }));
      onChange({
        nodes: [...state.nodes, ...copies],
        edges: [...state.edges, ...internalEdges],
      });
      // Single-copy case: focus the new node so the user can keep editing.
      // Multi-copy: leave selection as-is; React Flow keeps the originals
      // selected so the user can iterate on the same group.
      if (copies.length === 1) {
        setSelectedNodeId(copies[0].id);
        setSelectedEdgeId(null);
      }
    },
    [state, onChange],
  );

  const disconnectNode = useCallback(
    (id: string) => {
      // Strip every edge that touches the node — common ask when re-wiring
      // an existing graph without deleting the node itself.
      onChange({
        ...state,
        edges: state.edges.filter((e) => e.source !== id && e.target !== id),
      });
    },
    [state, onChange],
  );

  const selectedNode = state.nodes.find((n) => n.id === selectedNodeId) ?? null;
  const selectedEdge = state.edges.find((e) => e.id === selectedEdgeId) ?? null;

  // ----- right-click menus (2026-05-26 user request) -----------------------
  // One menu controller per surface (pane / node / edge). Single source of
  // truth for "what was right-clicked" so the menu content can act on it.
  const paneMenu = useContextMenu();
  const nodeMenu = useContextMenu();
  const edgeMenu = useContextMenu();
  // Remember the flow position of the pane right-click so 'Add … here' drops
  // the node exactly under the original cursor (the menu items fire after
  // the user has moved the mouse to pick).
  const paneTargetRef = useRef<{ x: number; y: number } | null>(null);
  const nodeTargetRef = useRef<string | null>(null);
  const edgeTargetRef = useRef<string | null>(null);

  const nodeForMenu = nodeTargetRef.current
    ? state.nodes.find((n) => n.id === nodeTargetRef.current)
    : null;

  // Cmd+D anywhere in the builder duplicates the current multi-selection.
  // Uses the same "skip when typing into an editable" guard as the undo
  // shortcut so the keystroke doesn't fight browser-native input behaviour.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod || e.key.toLowerCase() !== "d") return;
      const target = e.target;
      if (target instanceof HTMLElement) {
        if (
          target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable ||
          target.getAttribute("role") === "textbox"
        )
          return;
      }
      const ids = selectedIdsRef.current.nodeIds;
      if (ids.length === 0) return;
      e.preventDefault();
      duplicateNodes(ids);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [duplicateNodes]);

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <Palette mode={mode} variant={builderKind === "orchestration" ? "orchestration" : "graph"} />
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
            onRemoveNodes={removeNodes}
            onSelectionChange={(sel) => {
              selectedIdsRef.current = sel;
            }}
            onConnect={connect}
            onRemoveEdge={removeEdge}
            onMoveNode={moveNode}
            onDropOperator={addOperator}
            onDeselect={() => {
              setSelectedNodeId(null);
              setSelectedEdgeId(null);
            }}
            onPaneContextMenu={(e, flowPos) => {
              paneTargetRef.current = flowPos;
              paneMenu.openAt(e.clientX, e.clientY);
            }}
            onNodeContextMenu={(e, nodeId) => {
              nodeTargetRef.current = nodeId;
              nodeMenu.openAt(e.clientX, e.clientY);
            }}
            onEdgeContextMenu={(e, edgeId) => {
              edgeTargetRef.current = edgeId;
              edgeMenu.openAt(e.clientX, e.clientY);
            }}
          />
        </div>
        {dryRunPanel}
      </div>

      {/* --- pane context menu: add node at click point ------------------- */}
      <ContextMenu menu={paneMenu} minWidth={220}>
        <ContextMenuLabel>{t("graph.menuAddNode")}</ContextMenuLabel>
        {OPERATOR_KIND_GROUPS.map((group) => {
          // graph-only operators (join / aggregate) appear under transforms;
          // streaming filter excludes irrelevant source/sink in stream mode.
          const visibleCategories = group.categories
            .map((c) => ({
              ...c,
              specs: c.specs.filter((s) => operatorAllowedForMode(s, mode)),
            }))
            .filter((c) => c.specs.length > 0);
          if (visibleCategories.length === 0) return null;
          return (
            <ContextMenuSubmenu key={group.kind} label={group.label}>
              {visibleCategories.map((cat) => (
                <div key={cat.category}>
                  <ContextMenuLabel>{cat.category}</ContextMenuLabel>
                  {cat.specs.map((spec) => (
                    <ContextMenuItem
                      key={spec.id}
                      onSelect={() => addOperator(spec.id, paneTargetRef.current ?? undefined)}
                    >
                      {getOperatorLabel(spec, t)}
                    </ContextMenuItem>
                  ))}
                </div>
              ))}
            </ContextMenuSubmenu>
          );
        })}
      </ContextMenu>

      {/* --- node context menu ------------------------------------------- */}
      <ContextMenu menu={nodeMenu}>
        <ContextMenuItem
          icon={<EditIcon size={14} />}
          onSelect={() => nodeTargetRef.current && selectNode(nodeTargetRef.current)}
        >
          {t("graph.menuEdit")}
        </ContextMenuItem>
        <ContextMenuItem
          icon={<CopyIcon size={14} />}
          onSelect={() => nodeTargetRef.current && duplicateNode(nodeTargetRef.current)}
        >
          {t("graph.menuDuplicate")}
        </ContextMenuItem>
        <ContextMenuItem
          icon={<UnlinkIcon size={14} />}
          disabled={
            // Only enabled when the node actually has edges to strip.
            !nodeForMenu ||
            !state.edges.some(
              (e) => e.source === nodeForMenu.id || e.target === nodeForMenu.id,
            )
          }
          onSelect={() => nodeTargetRef.current && disconnectNode(nodeTargetRef.current)}
        >
          {t("graph.menuDisconnect")}
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          icon={<Trash2Icon size={14} />}
          destructive
          onSelect={() => nodeTargetRef.current && removeNode(nodeTargetRef.current)}
        >
          {t("graph.menuDelete")}
        </ContextMenuItem>
      </ContextMenu>

      {/* --- edge context menu ------------------------------------------- */}
      <ContextMenu menu={edgeMenu}>
        <ContextMenuItem
          icon={<GitBranchIcon size={14} />}
          onSelect={() => edgeTargetRef.current && selectEdge(edgeTargetRef.current)}
        >
          {t("graph.menuEditCondition")}
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          icon={<Trash2Icon size={14} />}
          destructive
          onSelect={() => edgeTargetRef.current && removeEdge(edgeTargetRef.current)}
        >
          {t("graph.menuDeleteEdge")}
        </ContextMenuItem>
      </ContextMenu>
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
