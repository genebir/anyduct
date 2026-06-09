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
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getNodesBounds,
  getViewportForBounds,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import Link from "next/link";
import {
  ArrowLeftIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CopyIcon,
  DatabaseIcon,
  ImageIcon,
  KeyIcon,
  LayoutGridIcon,
  LinkIcon,
  PlusIcon,
  TrashIcon,
  XIcon,
} from "lucide-react";
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
import { parseDamx } from "@/lib/damx";
import { autoLayout } from "@/lib/erd-layout";
import {
  columnDictionaryCsv,
  constraintSpecCsv,
  fullSpecMarkdown,
  mappingSpecCsv,
  tableDefinitionCsv,
} from "@/lib/erd-docs";
import { toPng } from "html-to-image";
import { erdApi } from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import type { Messages } from "@/lib/i18n/messages";

type Menu =
  | { x: number; y: number; kind: "pane" }
  | { x: number; y: number; kind: "node"; nodeId: string };

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

const DIALECTS = ["postgres", "mysql", "sqlite", "snowflake", "bigquery"];

function nodeLabel(tb: DesignTable, fkCols: Set<string>): React.ReactNode {
  return (
    <div className="w-full text-left">
      <div className="truncate rounded-t-[7px] border-b border-border-subtle bg-overlay px-2.5 py-1.5 font-mono text-[11px] font-semibold text-text">
        {tb.name}
      </div>
      <div>
        {tb.columns.map((c) => (
          <div key={c.name} className="flex items-center gap-1.5 border-b border-border-subtle/40 px-2.5 py-1 last:border-0">
            {c.pk ? (
              <KeyIcon size={10} className="shrink-0 text-warning">
                <title>PK</title>
              </KeyIcon>
            ) : fkCols.has(c.name) ? (
              <LinkIcon size={10} className="shrink-0 text-accent">
                <title>FK</title>
              </LinkIcon>
            ) : (
              <span className="inline-block w-[10px] shrink-0" />
            )}
            <span
              className={`flex-1 truncate font-mono text-[11px] ${fkCols.has(c.name) && !c.pk ? "text-accent" : "text-text"}`}
            >
              {c.name}
            </span>
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
  onRenameColumn,
  onDelete,
  onClose,
}: {
  table: DesignTable;
  t: Translate;
  onChange: (patch: Partial<DesignTable>) => void;
  onRenameColumn: (index: number, newName: string) => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const setColumn = (i: number, patch: Partial<DesignColumn>) => {
    onChange({ columns: table.columns.map((c, j) => (j === i ? { ...c, ...patch } : c)) });
  };
  return (
    <div className="flex w-72 shrink-0 flex-col gap-3 overflow-y-auto border-l border-border-subtle bg-surface p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
          {t("erdDesign.table")}
        </span>
        <button onClick={onClose} aria-label={t("common.close")} className="text-text-muted hover:text-text">
          <XIcon size={14} />
        </button>
      </div>
      <Input
        value={table.name}
        onChange={(e) => onChange({ name: e.target.value })}
        placeholder={t("erdDesign.tableName")}
      />
      <Input
        value={table.logical ?? ""}
        onChange={(e) => onChange({ logical: e.target.value })}
        placeholder={t("erdDesign.tableLogical")}
        className="text-sm"
      />
      <Input
        value={table.comment ?? ""}
        onChange={(e) => onChange({ comment: e.target.value })}
        placeholder={t("erdDesign.tableComment")}
        className="text-xs"
      />

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
          <div key={i} className="rounded-md border border-border-subtle/40 p-1">
            <div className="flex items-center gap-1">
              <button
                onClick={() => setExpandedIdx(expandedIdx === i ? null : i)}
                aria-label={t("erdDesign.colDetails")}
                title={t("erdDesign.colDetails")}
                className="text-text-muted hover:text-text"
              >
                {expandedIdx === i ? <ChevronDownIcon size={13} /> : <ChevronRightIcon size={13} />}
              </button>
              <Input
                value={c.name}
                onChange={(e) => onRenameColumn(i, e.target.value)}
                className="h-7 flex-1 text-xs"
              />
              <select
                value={c.type}
                onChange={(e) => setColumn(i, { type: e.target.value })}
                className="h-7 rounded-md border border-border-subtle bg-bg px-1 text-[11px] text-text"
              >
                {(ERD_TYPES as readonly string[]).includes(c.type) ? null : (
                  <option value={c.type}>{c.type}</option>
                )}
                {ERD_TYPES.map((ty) => (
                  <option key={ty} value={ty}>
                    {ty}
                  </option>
                ))}
              </select>
              <button
                onClick={() => setColumn(i, { notNull: !(c.notNull ?? false) })}
                aria-label={t("erdDesign.notNull")}
                title={t("erdDesign.notNull")}
                className={`px-0.5 text-[9px] font-bold ${
                  c.pk || c.notNull ? "text-accent" : "text-text-muted hover:text-text"
                }`}
              >
                NN
              </button>
              <button
                onClick={() => setColumn(i, { pk: !c.pk })}
                aria-label={t("erdDesign.pk")}
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
            {expandedIdx === i ? (
              <div className="mt-1 flex flex-col gap-1 pl-5">
                <Input
                  value={c.logical ?? ""}
                  onChange={(e) => setColumn(i, { logical: e.target.value })}
                  placeholder={t("erdDesign.colLogical")}
                  className="h-7 text-xs"
                />
                <Input
                  value={c.defaultValue ?? ""}
                  onChange={(e) => setColumn(i, { defaultValue: e.target.value })}
                  placeholder={t("erdDesign.colDefault")}
                  className="h-7 text-xs"
                />
                <Input
                  value={c.comment ?? ""}
                  onChange={(e) => setColumn(i, { comment: e.target.value })}
                  placeholder={t("erdDesign.colComment")}
                  className="h-7 text-xs"
                />
              </div>
            ) : null}
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

  // Debounced server autosave on change, with a status indicator so users
  // trust the (invisible) server persistence.
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle");
  useEffect(() => {
    if (!loaded || !ws?.id) return;
    const wsId = ws.id;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    setSaveState("saving");
    saveTimer.current = setTimeout(() => {
      erdApi
        .update(wsId, docId, { name: docName, design_json: design })
        .then(() => setSaveState("saved"))
        .catch(() => setSaveState("idle"));
    }, 700);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [ws?.id, docId, design, docName, loaded]);

  const nodes = useMemo<Node[]>(() => {
    // FK columns per table: any column that's the source of a relation.
    const fkByTable = new Map<string, Set<string>>();
    for (const r of design.relations) {
      const set = fkByTable.get(r.from) ?? new Set<string>();
      set.add(r.fromColumn);
      fkByTable.set(r.from, set);
    }
    return design.tables.map((tb) => ({
        id: tb.id,
        position: { x: tb.x, y: tb.y },
        data: { label: nodeLabel(tb, fkByTable.get(tb.id) ?? new Set()) },
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
      }));
  }, [design, selectedId]);

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

  // Delete/Backspace removes the selected table(s) or edge(s).
  const onNodesDelete = useCallback((deleted: Node[]) => {
    const ids = new Set(deleted.map((n) => n.id));
    setDesign((d) => ({
      tables: d.tables.filter((t) => !ids.has(t.id)),
      relations: d.relations.filter((r) => !ids.has(r.from) && !ids.has(r.to)),
    }));
    setSelectedId(null);
  }, []);

  const onEdgesDelete = useCallback((deleted: Edge[]) => {
    const ids = new Set(deleted.map((e) => e.id));
    setDesign((d) => ({ ...d, relations: d.relations.filter((r) => !ids.has(r.id)) }));
    setSelectedEdgeId(null);
  }, []);

  const updateRelation = (id: string, patch: Partial<ErdDesign["relations"][number]>) =>
    setDesign((d) => ({
      ...d,
      relations: d.relations.map((r) => (r.id === id ? { ...r, ...patch } : r)),
    }));

  const deleteRelation = (id: string) =>
    setDesign((d) => ({ ...d, relations: d.relations.filter((r) => r.id !== id) }));

  const updateTable = (id: string, patch: Partial<DesignTable>) =>
    setDesign((d) => ({ ...d, tables: d.tables.map((tb) => (tb.id === id ? { ...tb, ...patch } : tb)) }));

  // Rename a column AND keep any FK relation that references it in sync, so
  // the edge label (which shows the FK column name) follows the rename.
  const renameColumn = (tableId: string, index: number, newName: string) =>
    setDesign((d) => {
      const tbl = d.tables.find((t) => t.id === tableId);
      const oldName = tbl?.columns[index]?.name;
      const tables = d.tables.map((t) =>
        t.id === tableId
          ? { ...t, columns: t.columns.map((c, j) => (j === index ? { ...c, name: newName } : c)) }
          : t,
      );
      const relations =
        oldName == null
          ? d.relations
          : d.relations.map((r) =>
              r.from === tableId && r.fromColumn === oldName ? { ...r, fromColumn: newName } : r,
            );
      return { tables, relations };
    });

  const deleteTable = (id: string) =>
    setDesign((d) => ({
      tables: d.tables.filter((tb) => tb.id !== id),
      relations: d.relations.filter((r) => r.from !== id && r.to !== id),
    }));

  const rfRef = useRef<ReactFlowInstance<Node, Edge> | null>(null);
  const [layoutDir, setLayoutDir] = useState<"TB" | "LR">("TB");
  const onAutoLayout = (dir: "TB" | "LR" = layoutDir) => {
    if (design.tables.length === 0) return;
    setLayoutDir(dir);
    setDesign((d) => autoLayout(d, dir));
    // Re-fit after the new positions render.
    setTimeout(() => rfRef.current?.fitView({ padding: 0.2, duration: 300 }), 60);
  };

  const onExportPng = async () => {
    if (design.tables.length === 0) return;
    const el = document.querySelector<HTMLElement>(".react-flow__viewport");
    if (!el) return;
    const pad = 80;
    const bounds = getNodesBounds(rfNodes);
    const imgW = Math.min(Math.max(bounds.width + pad * 2, 600), 5000);
    const imgH = Math.min(Math.max(bounds.height + pad * 2, 400), 5000);
    const vp = getViewportForBounds(bounds, imgW, imgH, 0.2, 2, pad);
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
    try {
      const url = await toPng(el, {
        backgroundColor: bg ? `rgb(${bg})` : "#ffffff",
        width: imgW,
        height: imgH,
        style: {
          width: `${imgW}px`,
          height: `${imgH}px`,
          transform: `translate(${vp.x}px, ${vp.y}px) scale(${vp.zoom})`,
        },
      });
      const a = document.createElement("a");
      a.download = `${docName || "erd"}.png`;
      a.href = url;
      a.click();
    } catch {
      toast.error(t("erdDesign.exportImageError"));
    }
  };

  const onGenerateDoc = (kind: string) => {
    if (design.tables.length === 0) return;
    const base = (docName || "erd").replace(/[/\\?%*:|"<>]/g, "_");
    let content = "";
    let filename = "";
    let mime = "text/csv;charset=utf-8";
    if (kind === "columns") {
      content = columnDictionaryCsv(design);
      filename = `${base}_컬럼정의서.csv`;
    } else if (kind === "tables") {
      content = tableDefinitionCsv(design);
      filename = `${base}_테이블정의서.csv`;
    } else if (kind === "mapping") {
      content = mappingSpecCsv(design);
      filename = `${base}_매핑정의서.csv`;
    } else if (kind === "constraints") {
      content = constraintSpecCsv(design);
      filename = `${base}_제약인덱스정의서.csv`;
    } else if (kind === "markdown") {
      const today = new Date().toLocaleDateString();
      content = fullSpecMarkdown(design, docName || "ERD", today);
      filename = `${base}_데이터정의서.md`;
      mime = "text/markdown;charset=utf-8";
    } else {
      return;
    }
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    toast.success(t("erdDocs.generated", { name: filename }));
  };

  const onJumpToTable = (name: string) => {
    const tb = design.tables.find(
      (t) => t.name === name || t.name.toLowerCase() === name.toLowerCase(),
    );
    if (!tb) return;
    setSelectedId(tb.id);
    setSelectedEdgeId(null);
    rfRef.current?.setCenter(tb.x + 110, tb.y + 70, { zoom: 1.1, duration: 400 });
  };

  const damxRef = useRef<HTMLInputElement>(null);
  const onDamxFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const incoming = parseDamx(await file.arrayBuffer());
      if (incoming.tables.length === 0) {
        toast.error(t("erdDesign.damxEmpty"));
        return;
      }
      setDesign((d) => autoLayout(mergeDesign(d, incoming)));
      setTimeout(() => rfRef.current?.fitView({ padding: 0.2, duration: 300 }), 60);
      toast.success(t("erdDesign.damxImported", { n: incoming.tables.length }));
    } catch {
      toast.error(t("erdDesign.damxError"));
    }
  };

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

  const onCopyTableSql = async (id: string) => {
    const tb = design.tables.find((t) => t.id === id);
    if (!tb) return;
    const sql = toSql(
      { tables: [tb], relations: design.relations.filter((r) => r.from === id && r.to === id) },
      dialect,
    );
    try {
      await navigator.clipboard.writeText(sql);
      toast.success(t("erdDesign.tableSqlCopied", { name: tb.name }));
    } catch {
      toast.error(t("erdDesign.exportImageError"));
    }
  };

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
        {saveState !== "idle" ? (
          <span className="text-[11px] text-text-muted">
            {saveState === "saving" ? t("erdDoc.saving") : t("erdDoc.saved")}
          </span>
        ) : null}
        {design.tables.length > 0 ? (
          <span className="text-[11px] text-text-muted">
            {t("erdDesign.counts", {
              tables: design.tables.length,
              rels: design.relations.length,
            })}
          </span>
        ) : null}
        <span className="mx-1 h-5 w-px bg-border-subtle" />
        {design.tables.length > 8 ? (
          <>
            <input
              list="erd-table-search"
              placeholder={t("erdDesign.searchTable")}
              className="h-8 w-44 rounded-md border border-border-subtle bg-bg px-2 text-xs text-text"
              onChange={(e) => {
                if (design.tables.some((t) => t.name === e.target.value)) onJumpToTable(e.target.value);
              }}
            />
            <datalist id="erd-table-search">
              {design.tables.map((t) => (
                <option key={t.id} value={t.name} />
              ))}
            </datalist>
          </>
        ) : null}
        <Button size="sm" variant="secondary" onClick={onAddTable}>
          <PlusIcon size={14} />
          {t("erdDesign.addTable")}
        </Button>
        <Button size="sm" variant="secondary" onClick={() => setShowImport(true)} disabled={!ws?.id}>
          <DatabaseIcon size={14} />
          {t("erdDesign.import")}
        </Button>
        <Button size="sm" variant="secondary" onClick={() => damxRef.current?.click()}>
          <DatabaseIcon size={14} />
          {t("erdDesign.importDamx")}
        </Button>
        <input
          ref={damxRef}
          type="file"
          accept=".damx"
          className="hidden"
          onChange={(e) => void onDamxFile(e)}
        />
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
            onClick={() => onAutoLayout()}
            disabled={design.tables.length === 0}
          >
            <LayoutGridIcon size={14} />
            {t("erdDesign.autoLayout")}
          </Button>
          <select
            value={layoutDir}
            onChange={(e) => onAutoLayout(e.target.value as "TB" | "LR")}
            disabled={design.tables.length === 0}
            className="h-8 rounded-md border border-border-subtle bg-bg px-1 text-xs text-text"
            aria-label={t("erdDesign.layoutDir")}
            title={t("erdDesign.layoutDir")}
          >
            <option value="TB">{t("erdDesign.layoutTB")}</option>
            <option value="LR">{t("erdDesign.layoutLR")}</option>
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
            variant="secondary"
            onClick={() => void onExportPng()}
            disabled={design.tables.length === 0}
          >
            <ImageIcon size={14} />
            {t("erdDesign.exportImage")}
          </Button>
          <select
            value=""
            onChange={(e) => {
              onGenerateDoc(e.target.value);
              e.target.value = "";
            }}
            disabled={design.tables.length === 0}
            className="h-8 rounded-md border border-border-subtle bg-bg px-2 text-xs text-text"
            aria-label={t("erdDocs.generate")}
            title={t("erdDocs.generate")}
          >
            <option value="">{t("erdDocs.generate")}</option>
            <option value="columns">{t("erdDocs.columns")}</option>
            <option value="tables">{t("erdDocs.tables")}</option>
            <option value="mapping">{t("erdDocs.mapping")}</option>
            <option value="constraints">{t("erdDocs.constraints")}</option>
            <option value="markdown">{t("erdDocs.markdown")}</option>
          </select>
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
        <div className="relative min-w-0 flex-1 bg-bg">
          {loaded && design.tables.length === 0 ? (
            <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
              <div className="rounded-lg border border-border-subtle bg-surface/90 px-5 py-4 text-center text-sm text-text-muted shadow-sm">
                <DatabaseIcon size={22} className="mx-auto mb-2 text-text-muted" />
                <p className="font-medium text-text">{t("erdDesign.emptyTitle")}</p>
                <p className="mt-1">{t("erdDesign.emptyHint")}</p>
              </div>
            </div>
          ) : null}
          <ReactFlowProvider>
            <ReactFlow
              onInit={(inst) => {
                rfRef.current = inst;
              }}
              nodes={rfNodes}
              edges={rfEdges}
              edgeTypes={ERD_EDGE_TYPES}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodesDelete={onNodesDelete}
              onEdgesDelete={onEdgesDelete}
              deleteKeyCode={["Delete", "Backspace"]}
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
              {/* Graph-paper grid: fine 20px lines + bolder 100px lines. */}
              <Background
                id="erd-grid-minor"
                variant={BackgroundVariant.Lines}
                gap={20}
                lineWidth={1}
                color="rgb(var(--border-subtle) / 0.35)"
              />
              <Background
                id="erd-grid-major"
                variant={BackgroundVariant.Lines}
                gap={100}
                lineWidth={1.2}
                color="rgb(var(--border-subtle) / 0.7)"
              />
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
          </ReactFlowProvider>
        </div>
        {selected ? (
          <TablePanel
            table={selected}
            t={t}
            onChange={(patch) => updateTable(selected.id, patch)}
            onRenameColumn={(i, newName) => renameColumn(selected.id, i, newName)}
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
                  className="block w-full px-3 py-1.5 text-left text-text hover:bg-overlay"
                  onClick={() => {
                    void onCopyTableSql(menu.nodeId);
                    setMenu(null);
                  }}
                >
                  {t("erdDesign.copyTableSql")}
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
