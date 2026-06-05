"use client";

/**
 * Interactive ERD designer (Phase AGX). Draw tables, columns and
 * relationships by hand on an @xyflow/react canvas; export to SQL DDL.
 * Client-side only — the design auto-saves to localStorage per workspace
 * (server-backed saved diagrams are a follow-up).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import Link from "next/link";
import { ArrowLeftIcon, CopyIcon, DatabaseIcon, KeyIcon, PlusIcon, TrashIcon, XIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  addTable,
  connect,
  EMPTY_DESIGN,
  ERD_TYPES,
  mergeDesign,
  newId,
  type Cardinality,
  type DesignColumn,
  type DesignRelation,
  type DesignTable,
  type ErdDesign,
  toSql,
} from "@/lib/erd-design";
import { useLocale } from "@/components/providers/locale-provider";
import { ERD_EDGE_TYPES } from "@/components/erd/crowsfoot-edge";
import { ImportTablesDialog } from "@/components/erd/import-tables-dialog";
import { erdApi } from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import type { Messages } from "@/lib/i18n/messages";

type Menu =
  | { x: number; y: number; kind: "pane" }
  | { x: number; y: number; kind: "node"; nodeId: string };

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

const DIALECTS = ["postgres", "mysql", "sqlite", "snowflake", "bigquery"];

function nodeLabel(tb: DesignTable): React.ReactNode {
  return (
    <div className="w-full text-left">
      <div className="truncate rounded-t-[7px] border-b border-border-subtle bg-overlay px-2.5 py-1.5 font-mono text-[11px] font-semibold text-text">
        {tb.name}
      </div>
      <div>
        {tb.columns.map((c) => (
          <div key={c.name} className="flex items-center gap-1.5 border-b border-border-subtle/40 px-2.5 py-1 last:border-0">
            {c.pk ? (
              <KeyIcon size={10} className="shrink-0 text-warning" />
            ) : (
              <span className="inline-block w-[10px] shrink-0" />
            )}
            <span className="flex-1 truncate font-mono text-[11px] text-text">{c.name}</span>
            <span className="shrink-0 font-mono text-[10px] text-text-muted">{c.type}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function TablePanel({
  table,
  t,
  onChange,
  onDelete,
  onClose,
}: {
  table: DesignTable;
  t: Translate;
  onChange: (patch: Partial<DesignTable>) => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const setColumn = (i: number, patch: Partial<DesignColumn>) => {
    onChange({ columns: table.columns.map((c, j) => (j === i ? { ...c, ...patch } : c)) });
  };
  return (
    <div className="flex w-72 shrink-0 flex-col gap-3 border-l border-border-subtle bg-surface p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
          {t("erdDesign.table")}
        </span>
        <button onClick={onClose} aria-label={t("common.close")} className="text-text-muted hover:text-text">
          <XIcon size={14} />
        </button>
      </div>
      <Input value={table.name} onChange={(e) => onChange({ name: e.target.value })} />

      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wide text-text-muted">{t("erdDesign.columns")}</span>
        <Button
          size="sm"
          variant="ghost"
          onClick={() =>
            onChange({ columns: [...table.columns, { name: `col_${table.columns.length + 1}`, type: "TEXT", pk: false }] })
          }
        >
          <PlusIcon size={13} />
        </Button>
      </div>

      <div className="flex flex-col gap-1.5">
        {table.columns.map((c, i) => (
          <div key={i} className="flex items-center gap-1">
            <Input
              value={c.name}
              onChange={(e) => setColumn(i, { name: e.target.value })}
              className="h-7 flex-1 text-xs"
            />
            <select
              value={c.type}
              onChange={(e) => setColumn(i, { type: e.target.value })}
              className="h-7 rounded-md border border-border-subtle bg-bg px-1 text-[11px] text-text"
            >
              {ERD_TYPES.map((ty) => (
                <option key={ty} value={ty}>
                  {ty}
                </option>
              ))}
            </select>
            <button
              onClick={() => setColumn(i, { pk: !c.pk })}
              aria-label="primary key"
              title={t("erdDesign.pk")}
              className={c.pk ? "text-warning" : "text-text-muted hover:text-text"}
            >
              <KeyIcon size={13} />
            </button>
            <button
              onClick={() => onChange({ columns: table.columns.filter((_, j) => j !== i) })}
              aria-label={t("common.delete")}
              className="text-text-muted hover:text-error"
            >
              <TrashIcon size={13} />
            </button>
          </div>
        ))}
      </div>

      <Button size="sm" variant="ghost" onClick={onDelete} className="mt-2 self-start hover:text-error">
        <TrashIcon size={13} />
        {t("erdDesign.deleteTable")}
      </Button>
    </div>
  );
}

// Cardinality presets — value is "<sourceCard>:<targetCard>".
const CARD_OPTIONS: { value: string; source: Cardinality; target: Cardinality; label: string }[] = [
  { value: "one:one", source: "one", target: "one", label: "1 : 1" },
  { value: "one:many", source: "one", target: "many", label: "1 : N" },
  { value: "many:one", source: "many", target: "one", label: "N : 1" },
  { value: "many:many", source: "many", target: "many", label: "N : M" },
];

function EdgePanel({
  relation,
  fromName,
  toName,
  t,
  onChange,
  onDelete,
  onClose,
}: {
  relation: DesignRelation;
  fromName: string;
  toName: string;
  t: Translate;
  onChange: (patch: Partial<DesignRelation>) => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const current = `${relation.sourceCard ?? "many"}:${relation.targetCard ?? "one"}`;
  return (
    <div className="flex w-72 shrink-0 flex-col gap-3 border-l border-border-subtle bg-surface p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
          {t("erdEdge.title")}
        </span>
        <button onClick={onClose} aria-label={t("common.close")} className="text-text-muted hover:text-text">
          <XIcon size={14} />
        </button>
      </div>
      <div className="rounded-md border border-border-subtle bg-bg p-2 text-xs text-text-secondary">
        <span className="font-mono text-text">{fromName}</span>
        <span className="text-text-muted">.{relation.fromColumn} → </span>
        <span className="font-mono text-text">{toName}</span>
      </div>
      <label className="text-[11px] uppercase tracking-wide text-text-muted">
        {t("erdEdge.cardinality")}
      </label>
      <select
        value={current}
        onChange={(e) => {
          const opt = CARD_OPTIONS.find((o) => o.value === e.target.value);
          if (opt) onChange({ sourceCard: opt.source, targetCard: opt.target });
        }}
        className="h-8 rounded-md border border-border-subtle bg-bg px-2 text-sm text-text"
      >
        {CARD_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label} ({fromName} : {toName})
          </option>
        ))}
      </select>
      <Button size="sm" variant="ghost" onClick={onDelete} className="mt-2 self-start hover:text-error">
        <TrashIcon size={13} />
        {t("erdEdge.delete")}
      </Button>
    </div>
  );
}

export function ErdDesigner({ slug, docId }: { slug: string; docId: string }) {
  const { t } = useLocale();
  const ws = useWorkspaceFromSlug(slug);
  const [design, setDesign] = useState<ErdDesign>(EMPTY_DESIGN);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [dialect, setDialect] = useState("postgres");
  const [sql, setSql] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [menu, setMenu] = useState<Menu | null>(null);
  const [docName, setDocName] = useState("");
  const [renaming, setRenaming] = useState(false);

  // Load the requested diagram from the server (ADR-0090).
  useEffect(() => {
    if (!ws?.id) return;
    let cancelled = false;
    setLoaded(false);
    erdApi
      .get(ws.id, docId)
      .then((d) => {
        if (cancelled) return;
        setDocName(d.name);
        setDesign(d.design_json ?? EMPTY_DESIGN);
        setSelectedId(null);
        setSelectedEdgeId(null);
        setLoaded(true);
      })
      .catch(() => {
        if (cancelled) return;
        setDesign(EMPTY_DESIGN);
        setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [ws?.id, docId]);

  // Debounced server autosave on change.
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!loaded || !ws?.id) return;
    const wsId = ws.id;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      void erdApi.update(wsId, docId, { name: docName, design_json: design }).catch(() => {});
    }, 700);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [ws?.id, docId, design, docName, loaded]);

  const nodes = useMemo<Node[]>(
    () =>
      design.tables.map((tb) => ({
        id: tb.id,
        position: { x: tb.x, y: tb.y },
        data: { label: nodeLabel(tb) },
        selected: tb.id === selectedId,
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        style: {
          width: 220,
          padding: 0,
          borderRadius: 8,
          border:
            tb.id === selectedId
              ? "2px solid rgb(var(--accent))"
              : "1px solid rgb(var(--border-subtle))",
          background: "rgb(var(--bg-elevated))",
          color: "rgb(var(--text))",
        },
      })),
    [design, selectedId],
  );

  const edges = useMemo<Edge[]>(
    () =>
      design.relations.map((r) => ({
        id: r.id,
        source: r.from,
        target: r.to,
        label: r.fromColumn,
        type: "crowsfoot",
        data: { sourceCard: r.sourceCard ?? "many", targetCard: r.targetCard ?? "one" },
        style: {
          stroke: "rgb(var(--accent))",
          strokeWidth: r.id === selectedEdgeId ? 2.5 : 1.5,
        },
        labelStyle: { fontSize: 10, fill: "rgb(var(--text-muted))" },
      })),
    [design, selectedEdgeId],
  );

  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(nodes);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState(edges);
  useEffect(() => setRfNodes(nodes), [nodes, setRfNodes]);
  useEffect(() => setRfEdges(edges), [edges, setRfEdges]);

  const onConnect = useCallback((c: Connection) => {
    if (c.source && c.target) setDesign((d) => connect(d, c.source!, c.target!));
  }, []);

  const onNodeDragStop = useCallback((_e: unknown, node: Node) => {
    setDesign((d) => ({
      ...d,
      tables: d.tables.map((tb) =>
        tb.id === node.id ? { ...tb, x: node.position.x, y: node.position.y } : tb,
      ),
    }));
  }, []);

  const onNodeClick: NodeMouseHandler = (_e, node) => {
    setSelectedId(node.id);
    setSelectedEdgeId(null);
  };

  const updateRelation = (id: string, patch: Partial<ErdDesign["relations"][number]>) =>
    setDesign((d) => ({
      ...d,
      relations: d.relations.map((r) => (r.id === id ? { ...r, ...patch } : r)),
    }));

  const deleteRelation = (id: string) =>
    setDesign((d) => ({ ...d, relations: d.relations.filter((r) => r.id !== id) }));

  const updateTable = (id: string, patch: Partial<DesignTable>) =>
    setDesign((d) => ({ ...d, tables: d.tables.map((tb) => (tb.id === id ? { ...tb, ...patch } : tb)) }));

  const deleteTable = (id: string) =>
    setDesign((d) => ({
      tables: d.tables.filter((tb) => tb.id !== id),
      relations: d.relations.filter((r) => r.from !== id && r.to !== id),
    }));

  const onAddTable = () =>
    setDesign((d) => {
      const n = d.tables.length;
      return addTable(d, `table_${n + 1}`, 60 + (n % 4) * 280, 60 + Math.floor(n / 4) * 220);
    });

  const duplicateTable = (id: string) =>
    setDesign((d) => {
      const src = d.tables.find((tb) => tb.id === id);
      if (!src) return d;
      const copy: DesignTable = {
        ...src,
        id: newId("tbl"),
        name: `${src.name}_copy`,
        x: src.x + 40,
        y: src.y + 40,
        columns: src.columns.map((c) => ({ ...c })),
      };
      return { ...d, tables: [...d.tables, copy] };
    });

  const onPaneContextMenu = useCallback((e: React.MouseEvent | MouseEvent) => {
    e.preventDefault();
    setMenu({ x: e.clientX, y: e.clientY, kind: "pane" });
  }, []);

  const onNodeContextMenu = useCallback((e: React.MouseEvent, node: Node) => {
    e.preventDefault();
    setSelectedId(node.id);
    setMenu({ x: e.clientX, y: e.clientY, kind: "node", nodeId: node.id });
  }, []);

  const selected = design.tables.find((tb) => tb.id === selectedId) ?? null;
  const selectedEdge = design.relations.find((r) => r.id === selectedEdgeId) ?? null;
  const tableName = (id: string) => design.tables.find((tb) => tb.id === id)?.name ?? "?";

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-subtle bg-surface px-4 py-2">
        <Link href={`/w/${slug}/erd`}>
          <Button size="sm" variant="ghost" aria-label={t("erdDoc.backToList")}>
            <ArrowLeftIcon size={14} />
          </Button>
        </Link>
        {renaming ? (
          <Input
            autoFocus
            value={docName}
            onChange={(e) => setDocName(e.target.value)}
            onBlur={() => {
              if (!docName.trim()) setDocName("Untitled");
              setRenaming(false);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            }}
            className="h-8 w-44 text-sm"
          />
        ) : (
          <button
            className="text-sm font-semibold text-text hover:underline"
            onClick={() => setRenaming(true)}
            title={t("erdDoc.rename")}
          >
            {docName || "Untitled"}
          </button>
        )}
        <span className="mx-1 h-5 w-px bg-border-subtle" />
        <Button size="sm" variant="secondary" onClick={onAddTable}>
          <PlusIcon size={14} />
          {t("erdDesign.addTable")}
        </Button>
        <Button size="sm" variant="secondary" onClick={() => setShowImport(true)} disabled={!ws?.id}>
          <DatabaseIcon size={14} />
          {t("erdDesign.import")}
        </Button>
        <span className="text-xs text-text-muted">{t("erdDesign.connectHint")}</span>
        <div className="ml-auto flex items-center gap-2">
          <select
            value={dialect}
            onChange={(e) => setDialect(e.target.value)}
            className="h-8 rounded-md border border-border-subtle bg-bg px-2 text-xs text-text"
          >
            {DIALECTS.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setSql(toSql(design, dialect))}
            disabled={design.tables.length === 0}
          >
            {t("erdDesign.exportSql")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              if (design.tables.length === 0) return;
              setDesign(EMPTY_DESIGN);
              setSelectedId(null);
            }}
            className="hover:text-error"
          >
            {t("erdDesign.clear")}
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 bg-bg">
          <ReactFlowProvider>
            <ReactFlow
              nodes={rfNodes}
              edges={rfEdges}
              edgeTypes={ERD_EDGE_TYPES}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeDragStop={onNodeDragStop}
              onNodeClick={onNodeClick}
              onEdgeClick={(_e, edge) => {
                setSelectedEdgeId(edge.id);
                setSelectedId(null);
              }}
              onPaneClick={() => {
                setSelectedId(null);
                setSelectedEdgeId(null);
                setMenu(null);
              }}
              onPaneContextMenu={onPaneContextMenu}
              onNodeContextMenu={onNodeContextMenu}
              fitView
              proOptions={{ hideAttribution: true }}
            >
              <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgb(var(--border-subtle) / 0.6)" />
              <Controls showInteractive={false} className="!rounded-md !border !border-border-subtle !bg-elevated" />
            </ReactFlow>
          </ReactFlowProvider>
        </div>
        {selected ? (
          <TablePanel
            table={selected}
            t={t}
            onChange={(patch) => updateTable(selected.id, patch)}
            onDelete={() => {
              deleteTable(selected.id);
              setSelectedId(null);
            }}
            onClose={() => setSelectedId(null)}
          />
        ) : selectedEdge ? (
          <EdgePanel
            relation={selectedEdge}
            fromName={tableName(selectedEdge.from)}
            toName={tableName(selectedEdge.to)}
            t={t}
            onChange={(patch) => updateRelation(selectedEdge.id, patch)}
            onDelete={() => {
              deleteRelation(selectedEdge.id);
              setSelectedEdgeId(null);
            }}
            onClose={() => setSelectedEdgeId(null)}
          />
        ) : null}
      </div>

      {sql !== null ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setSql(null)}>
          <div
            className="flex max-h-[80vh] w-full max-w-2xl flex-col gap-3 rounded-lg border border-border-subtle bg-surface p-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-text">{t("erdDesign.sqlTitle", { dialect })}</span>
              <button onClick={() => setSql(null)} aria-label={t("common.close")} className="text-text-muted hover:text-text">
                <XIcon size={16} />
              </button>
            </div>
            <textarea
              readOnly
              value={sql}
              className="h-80 w-full resize-none rounded-md border border-border-subtle bg-bg p-2 font-mono text-xs text-text"
            />
            <Button
              size="sm"
              variant="secondary"
              className="self-end"
              onClick={() => {
                void navigator.clipboard.writeText(sql);
                toast.success(t("erdDesign.copied"));
              }}
            >
              <CopyIcon size={14} />
              {t("erdDesign.copy")}
            </Button>
          </div>
        </div>
      ) : null}

      {showImport && ws?.id ? (
        <ImportTablesDialog
          workspaceId={ws.id}
          onClose={() => setShowImport(false)}
          onImport={(incoming) => setDesign((d) => mergeDesign(d, incoming))}
        />
      ) : null}

      {menu ? (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setMenu(null)} />
          <div
            className="fixed z-50 min-w-36 rounded-md border border-border-subtle bg-elevated py-1 text-sm shadow-lg"
            style={{ left: menu.x, top: menu.y }}
          >
            {menu.kind === "pane" ? (
              <button
                className="block w-full px-3 py-1.5 text-left text-text hover:bg-overlay"
                onClick={() => {
                  onAddTable();
                  setMenu(null);
                }}
              >
                {t("erdDesign.addTable")}
              </button>
            ) : (
              <>
                <button
                  className="block w-full px-3 py-1.5 text-left text-text hover:bg-overlay"
                  onClick={() => {
                    setSelectedId(menu.nodeId);
                    setMenu(null);
                  }}
                >
                  {t("erdDesign.editTable")}
                </button>
                <button
                  className="block w-full px-3 py-1.5 text-left text-text hover:bg-overlay"
                  onClick={() => {
                    duplicateTable(menu.nodeId);
                    setMenu(null);
                  }}
                >
                  {t("erdDesign.duplicateTable")}
                </button>
                <button
                  className="block w-full px-3 py-1.5 text-left text-error hover:bg-overlay"
                  onClick={() => {
                    deleteTable(menu.nodeId);
                    if (selectedId === menu.nodeId) setSelectedId(null);
                    setMenu(null);
                  }}
                >
                  {t("erdDesign.deleteTable")}
                </button>
              </>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}
