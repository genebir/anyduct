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
  NodeResizer,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getNodesBounds,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type NodeProps,
  type NodeTypes,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import Link from "next/link";
import {
  AlertTriangleIcon,
  ArrowDownIcon,
  ArrowLeftIcon,
  ArrowUpIcon,
  CheckCircle2Icon,
  Table2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  CopyIcon,
  DatabaseIcon,
  KeyIcon,
  LayoutGridIcon,
  LinkIcon,
  PlusIcon,
  TrashIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import {
  addTable,
  connect,
  EMPTY_DESIGN,
  ERD_TYPES,
  mergeDesign,
  newId,
  SHAPE_COLORS,
  type Cardinality,
  type DesignColumn,
  type DesignRelation,
  type DesignTable,
  type ErdDesign,
  type ErdShape,
  toSql,
} from "@/lib/erd-design";
import { useLocale } from "@/components/providers/locale-provider";
import { ERD_EDGE_TYPES } from "@/components/erd/crowsfoot-edge";
import { ImportTablesDialog } from "@/components/erd/import-tables-dialog";
import { ImportDdlDialog } from "@/components/erd/import-ddl-dialog";
import { parseDamx } from "@/lib/damx";
import { autoLayout, removeOverlaps } from "@/lib/erd-layout";
import { validateErd } from "@/lib/erd-validate";
import {
  columnDictionaryCsv,
  constraintSpecCsv,
  fullSpecMarkdown,
  mappingSpecCsv,
  tableDefinitionCsv,
} from "@/lib/erd-docs";
import { toPng } from "html-to-image";
import { strToU8, zipSync } from "fflate";
import { erdApi } from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import type { Messages } from "@/lib/i18n/messages";

type Menu =
  | { x: number; y: number; kind: "pane" }
  | { x: number; y: number; kind: "node"; nodeId: string };

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

const DIALECTS = ["postgres", "mysql", "sqlite", "snowflake", "bigquery"];

const SHAPE_VAR: Record<string, string> = {
  muted: "--text-muted",
  accent: "--accent",
  success: "--success",
  warning: "--warning",
  error: "--error",
};

/** Background annotation node (grouping box / memo), drawn behind tables. */
function ShapeNode({ data, selected }: NodeProps) {
  const { shape, onResize } = data as {
    shape: ErdShape;
    onResize?: (id: string, p: { x: number; y: number; width: number; height: number }) => void;
  };
  const v = SHAPE_VAR[shape.color ?? "muted"] ?? "--text-muted";
  const ring = selected ? "outline outline-2 outline-offset-2 outline-accent" : "";
  const resizer = selected ? (
    <NodeResizer
      minWidth={shape.kind === "text" ? 60 : 80}
      minHeight={shape.kind === "text" ? 24 : 48}
      onResizeEnd={(_e, p) => onResize?.(shape.id, p)}
      lineClassName="!border-accent"
      handleClassName="!h-2 !w-2 !rounded-sm !border-accent !bg-surface"
    />
  ) : null;
  if (shape.kind === "text") {
    return (
      <>
        {resizer}
        <div
          className={`flex h-full w-full items-center whitespace-pre-wrap px-2 text-sm font-semibold ${ring}`}
          style={{ color: `rgb(var(${v}))` }}
        >
          {shape.text || "메모"}
        </div>
      </>
    );
  }
  return (
    <>
      {resizer}
      <div
        className={`h-full w-full rounded-lg border-2 ${ring}`}
        style={{ borderColor: `rgb(var(${v}) / 0.5)`, background: `rgb(var(${v}) / 0.08)` }}
      >
        {shape.text ? (
          <div className="px-2 py-1 text-xs font-semibold" style={{ color: `rgb(var(${v}))` }}>
            {shape.text}
          </div>
        ) : null}
      </div>
    </>
  );
}

const NODE_TYPES: NodeTypes = { shape: ShapeNode };

type NameMode = "physical" | "logical" | "both";

/** Render a name per the physical/logical/both display mode (logical falls
 *  back to physical when absent, so logical mode is never blank). */
function displayName(physical: string, logical: string | undefined, mode: NameMode): string {
  const log = logical?.trim();
  if (mode === "physical" || !log) return physical;
  if (mode === "logical") return log;
  return `${physical} · ${log}`;
}

function nodeLabel(tb: DesignTable, fkCols: Set<string>, mode: NameMode, scale: number): React.ReactNode {
  const nameSize = Math.round(11 * scale);
  const typeSize = Math.round(10 * scale);
  const icon = Math.round(10 * scale);
  return (
    <div className="w-full overflow-hidden text-left">
      <div className="flex items-center gap-1.5 rounded-t-[7px] border-b-2 border-accent/40 bg-accent/10 px-2.5 py-1.5">
        <Table2Icon size={Math.round(11 * scale)} className="shrink-0 text-accent" />
        <span className="truncate font-mono font-semibold text-text" style={{ fontSize: nameSize }}>
          {displayName(tb.name, tb.logical, mode)}
        </span>
      </div>
      <div>
        {tb.columns.map((c, ci) => {
          const isFk = fkCols.has(c.name) && !c.pk;
          const tip = [
            c.logical ? `${c.name} (${c.logical})` : c.name,
            c.type,
            c.pk ? "PK" : "",
            c.notNull && !c.pk ? "NOT NULL" : "",
            c.comment ? `\n${c.comment}` : "",
          ]
            .filter(Boolean)
            .join(" · ")
            .replace(" · \n", "\n");
          return (
            <div
              key={`${c.name}-${ci}`}
              title={tip}
              className={`flex items-center gap-1.5 border-b border-border-subtle/30 px-2.5 py-1 last:border-0 ${
                c.pk ? "bg-warning/5" : ""
              }`}
            >
              {c.pk ? (
                <KeyIcon size={icon} className="shrink-0 text-warning">
                  <title>PK</title>
                </KeyIcon>
              ) : isFk ? (
                <LinkIcon size={icon} className="shrink-0 text-accent">
                  <title>FK</title>
                </LinkIcon>
              ) : (
                <span className="inline-block shrink-0" style={{ width: icon }} />
              )}
              <span
                className={`flex-1 truncate font-mono ${
                  c.pk ? "font-semibold text-text" : isFk ? "text-accent" : "text-text"
                }`}
                style={{ fontSize: nameSize }}
              >
                {displayName(c.name, c.logical, mode)}
                {c.notNull && !c.pk ? <span className="text-error" title="NOT NULL"> *</span> : null}
              </span>
              <span className="shrink-0 font-mono text-text-muted" style={{ fontSize: typeSize }}>
                {c.type}
              </span>
            </div>
          );
        })}
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
  const moveColumn = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= table.columns.length) return;
    const cols = [...table.columns];
    [cols[i], cols[j]] = [cols[j], cols[i]];
    onChange({ columns: cols });
    setExpandedIdx(j);
  };
  const fieldLabel = (s: string) => (
    <span className="mb-0.5 block text-[10px] font-medium uppercase tracking-wide text-text-muted">{s}</span>
  );
  return (
    <div className="flex w-80 shrink-0 flex-col gap-3 overflow-y-auto border-l border-border-subtle bg-surface p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
          {t("erdDesign.table")}
        </span>
        <button onClick={onClose} aria-label={t("common.close")} className="text-text-muted hover:text-text">
          <XIcon size={14} />
        </button>
      </div>
      <div className="flex gap-2">
        <label className="flex-1">
          {fieldLabel(t("erdDesign.tablePhysical"))}
          <Input
            value={table.name}
            onChange={(e) => onChange({ name: e.target.value })}
            placeholder={t("erdDesign.tableName")}
            className="h-8 w-full"
          />
        </label>
        <label className="flex-1">
          {fieldLabel(t("erdDesign.tableLogicalShort"))}
          <Input
            value={table.logical ?? ""}
            onChange={(e) => onChange({ logical: e.target.value })}
            placeholder={t("erdDesign.tableLogicalShort")}
            className="h-8 w-full"
          />
        </label>
      </div>
      <label className="block">
        {fieldLabel(t("erdDesign.tableComment"))}
        <textarea
          value={table.comment ?? ""}
          onChange={(e) => onChange({ comment: e.target.value })}
          placeholder={t("erdDesign.tableComment")}
          rows={2}
          className="w-full resize-none rounded-md border border-border-subtle bg-bg px-2 py-1.5 text-xs text-text focus-visible:border-accent focus-visible:outline-none"
        />
      </label>

      <div className="flex items-center justify-between border-t border-border-subtle pt-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-text-secondary">
          {t("erdDesign.columns")} ({table.columns.length})
        </span>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            onChange({ columns: [...table.columns, { name: `col_${table.columns.length + 1}`, type: "TEXT", pk: false }] });
            setExpandedIdx(table.columns.length);
          }}
        >
          <PlusIcon size={13} />
          {t("erdDesign.addColumn")}
        </Button>
      </div>

      <div className="flex flex-col gap-2">
        {table.columns.map((c, i) => (
          <div key={i} className="rounded-md border border-border-subtle/50 bg-bg/40 p-2">
            {/* Row 1: column physical name — full width for comfortable editing. */}
            <Input
              value={c.name}
              onChange={(e) => onRenameColumn(i, e.target.value)}
              placeholder={t("erdDesign.colPhysical")}
              className="h-8 w-full font-mono text-xs"
            />
            {/* Row 2: type + PK/NN flags + details/delete. */}
            <div className="mt-1.5 flex items-center gap-1">
              <select
                value={c.type}
                onChange={(e) => setColumn(i, { type: e.target.value })}
                className="h-7 min-w-0 flex-1 rounded-md border border-border-subtle bg-bg px-1 text-[11px] text-text"
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
                onClick={() => setColumn(i, { pk: !c.pk })}
                aria-label={t("erdDesign.pk")}
                title={t("erdDesign.pk")}
                className={`rounded px-1.5 py-1 text-[10px] font-bold ${
                  c.pk ? "bg-warning/15 text-warning" : "text-text-muted hover:bg-overlay"
                }`}
              >
                PK
              </button>
              <button
                onClick={() => setColumn(i, { notNull: !(c.notNull ?? false) })}
                aria-label={t("erdDesign.notNull")}
                title={t("erdDesign.notNull")}
                className={`rounded px-1.5 py-1 text-[10px] font-bold ${
                  c.pk || c.notNull ? "bg-accent/15 text-accent" : "text-text-muted hover:bg-overlay"
                }`}
              >
                NN
              </button>
              <button
                onClick={() => setExpandedIdx(expandedIdx === i ? null : i)}
                aria-label={t("erdDesign.colDetails")}
                title={t("erdDesign.colDetails")}
                className="rounded p-1 text-text-muted hover:bg-overlay hover:text-text"
              >
                {expandedIdx === i ? <ChevronDownIcon size={14} /> : <ChevronRightIcon size={14} />}
              </button>
              <button
                onClick={() => onChange({ columns: table.columns.filter((_, j) => j !== i) })}
                aria-label={t("common.delete")}
                className="rounded p-1 text-text-muted hover:bg-overlay hover:text-error"
              >
                <TrashIcon size={14} />
              </button>
            </div>
            {expandedIdx === i ? (
              <div className="mt-2 flex flex-col gap-1.5 border-t border-border-subtle/50 pt-2">
                <div className="flex items-center justify-end gap-1">
                  <button
                    onClick={() => moveColumn(i, -1)}
                    disabled={i === 0}
                    aria-label={t("erdDesign.moveUp")}
                    title={t("erdDesign.moveUp")}
                    className="rounded p-1 text-text-muted hover:bg-overlay hover:text-text disabled:opacity-30"
                  >
                    <ArrowUpIcon size={13} />
                  </button>
                  <button
                    onClick={() => moveColumn(i, 1)}
                    disabled={i === table.columns.length - 1}
                    aria-label={t("erdDesign.moveDown")}
                    title={t("erdDesign.moveDown")}
                    className="rounded p-1 text-text-muted hover:bg-overlay hover:text-text disabled:opacity-30"
                  >
                    <ArrowDownIcon size={13} />
                  </button>
                </div>
                <label>
                  {fieldLabel(t("erdDesign.colLogical"))}
                  <Input
                    value={c.logical ?? ""}
                    onChange={(e) => setColumn(i, { logical: e.target.value })}
                    placeholder={t("erdDesign.colLogical")}
                    className="h-7 w-full text-xs"
                  />
                </label>
                <label>
                  {fieldLabel(t("erdDesign.colDefault"))}
                  <Input
                    value={c.defaultValue ?? ""}
                    onChange={(e) => setColumn(i, { defaultValue: e.target.value })}
                    placeholder={t("erdDesign.colDefault")}
                    className="h-7 w-full text-xs"
                  />
                </label>
                <label>
                  {fieldLabel(t("erdDesign.colComment"))}
                  <textarea
                    value={c.comment ?? ""}
                    onChange={(e) => setColumn(i, { comment: e.target.value })}
                    placeholder={t("erdDesign.colComment")}
                    rows={2}
                    className="w-full resize-none rounded-md border border-border-subtle bg-bg px-2 py-1 text-xs text-text focus-visible:border-accent focus-visible:outline-none"
                  />
                </label>
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

function ShapePanel({
  shape,
  t,
  onChange,
  onDelete,
  onClose,
}: {
  shape: ErdShape;
  t: Translate;
  onChange: (patch: Partial<ErdShape>) => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  return (
    <div className="flex w-72 shrink-0 flex-col gap-3 border-l border-border-subtle bg-surface p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
          {shape.kind === "rect" ? t("erdShape.rect") : t("erdShape.text")}
        </span>
        <button onClick={onClose} aria-label={t("common.close")} className="text-text-muted hover:text-text">
          <XIcon size={14} />
        </button>
      </div>
      <textarea
        value={shape.text ?? ""}
        onChange={(e) => onChange({ text: e.target.value })}
        placeholder={t("erdShape.label")}
        rows={shape.kind === "text" ? 2 : 3}
        className="w-full resize-none rounded-md border border-border-subtle bg-bg p-2 text-sm text-text"
      />
      <div>
        <span className="text-[11px] uppercase tracking-wide text-text-muted">{t("erdShape.color")}</span>
        <div className="mt-1 flex gap-1.5">
          {SHAPE_COLORS.map((c) => (
            <button
              key={c}
              onClick={() => onChange({ color: c })}
              aria-label={c}
              className={`h-6 w-6 rounded-full border-2 ${(shape.color ?? "muted") === c ? "border-text" : "border-transparent"}`}
              style={{ background: `rgb(var(${SHAPE_VAR[c]}) / 0.6)` }}
            />
          ))}
        </div>
      </div>
      <div className="flex gap-2">
        <label className="flex-1 text-[11px] text-text-muted">
          {t("erdShape.width")}
          <input
            type="number"
            value={shape.width}
            min={40}
            onChange={(e) => onChange({ width: Math.max(40, Number(e.target.value) || 0) })}
            className="mt-0.5 h-8 w-full rounded-md border border-border-subtle bg-bg px-2 text-xs text-text"
          />
        </label>
        <label className="flex-1 text-[11px] text-text-muted">
          {t("erdShape.height")}
          <input
            type="number"
            value={shape.height}
            min={24}
            onChange={(e) => onChange({ height: Math.max(24, Number(e.target.value) || 0) })}
            className="mt-0.5 h-8 w-full rounded-md border border-border-subtle bg-bg px-2 text-xs text-text"
          />
        </label>
      </div>
      <Button size="sm" variant="ghost" onClick={onDelete} className="mt-2 self-start hover:text-error">
        <TrashIcon size={13} />
        {t("common.delete")}
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
  const [selectedShapeId, setSelectedShapeId] = useState<string | null>(null);
  const [nameMode, setNameMode] = useState<NameMode>("physical");
  const [dialect, setDialect] = useState("postgres");
  const [sql, setSql] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [showDdl, setShowDdl] = useState(false);
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

  // Base nodes: the expensive part (per-column label JSX). Depends only on the
  // design + name mode — NOT on selection — so clicking a node doesn't rebuild
  // every table's label (matters for 300+ table imports).
  const baseNodes = useMemo<Node[]>(() => {
    const fkByTable = new Map<string, Set<string>>();
    for (const r of design.relations) {
      const set = fkByTable.get(r.from) ?? new Set<string>();
      set.add(r.fromColumn);
      fkByTable.set(r.from, set);
    }
    const scale = design.fontScale ?? 1;
    return design.tables.map((tb) => ({
      id: tb.id,
      position: { x: tb.x, y: tb.y },
      data: { label: nodeLabel(tb, fkByTable.get(tb.id) ?? new Set(), nameMode, scale) },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      style: {
        width: Math.round(240 * scale),
        padding: 0,
        borderRadius: 8,
        background: "rgb(var(--bg-elevated))",
        color: "rgb(var(--text))",
      },
    }));
  }, [design, nameMode]);

  const handleShapeResize = useCallback(
    (id: string, p: { x: number; y: number; width: number; height: number }) =>
      setDesign((d) => ({
        ...d,
        shapes: (d.shapes ?? []).map((s) =>
          s.id === id ? { ...s, x: p.x, y: p.y, width: Math.round(p.width), height: Math.round(p.height) } : s,
        ),
      })),
    [],
  );

  // Background shapes (grouping boxes / memos) rendered BEHIND tables.
  const shapeNodes = useMemo<Node[]>(
    () =>
      (design.shapes ?? []).map((s) => ({
        id: s.id,
        type: "shape",
        position: { x: s.x, y: s.y },
        data: { shape: s, onResize: handleShapeResize },
        selected: s.id === selectedShapeId,
        zIndex: -1,
        style: { width: s.width, height: s.height },
      })),
    [design.shapes, selectedShapeId, handleShapeResize],
  );

  // Cheap pass: apply selection highlight without recomputing labels. Shapes
  // first in the array (+ zIndex -1) so tables always sit on top.
  const nodes = useMemo<Node[]>(
    () => [
      ...shapeNodes,
      ...baseNodes.map((n) => ({
        ...n,
        selected: n.id === selectedId,
        style: {
          ...n.style,
          border:
            n.id === selectedId
              ? "2px solid rgb(var(--accent))"
              : "1px solid rgb(var(--border-subtle))",
        },
      })),
    ],
    [baseNodes, shapeNodes, selectedId],
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
    setDesign((d) => {
      if (node.type === "shape") {
        return {
          ...d,
          shapes: (d.shapes ?? []).map((s) =>
            s.id === node.id ? { ...s, x: node.position.x, y: node.position.y } : s,
          ),
        };
      }
      return {
        ...d,
        tables: d.tables.map((tb) =>
          tb.id === node.id ? { ...tb, x: node.position.x, y: node.position.y } : tb,
        ),
      };
    });
  }, []);

  const onNodeClick: NodeMouseHandler = (_e, node) => {
    if (node.type === "shape") {
      setSelectedShapeId(node.id);
      setSelectedId(null);
      setSelectedEdgeId(null);
      return;
    }
    setSelectedId(node.id);
    setSelectedShapeId(null);
    setSelectedEdgeId(null);
  };

  // Delete/Backspace removes the selected table(s), shape(s) or edge(s).
  const onNodesDelete = useCallback((deleted: Node[]) => {
    const ids = new Set(deleted.map((n) => n.id));
    setDesign((d) => ({
      ...d,
      tables: d.tables.filter((t) => !ids.has(t.id)),
      relations: d.relations.filter((r) => !ids.has(r.from) && !ids.has(r.to)),
      shapes: (d.shapes ?? []).filter((s) => !ids.has(s.id)),
    }));
    setSelectedId(null);
    setSelectedShapeId(null);
  }, []);

  // Confirm before ANY canvas deletion (tables, shapes, relationship edges) —
  // the designer autosaves, so an accidental Delete keypress would be
  // destructive with no undo.
  const [pendingNodeDelete, setPendingNodeDelete] = useState<{ nodes: string[]; edges: string[] } | null>(null);
  const onBeforeDelete = useCallback(
    ({ nodes: delNodes, edges: delEdges }: { nodes: Node[]; edges: Edge[] }) => {
      if (delNodes.length === 0 && delEdges.length === 0) return Promise.resolve(true);
      setPendingNodeDelete({ nodes: delNodes.map((n) => n.id), edges: delEdges.map((e) => e.id) });
      return Promise.resolve(false);
    },
    [],
  );
  const confirmNodeDelete = () => {
    const pending = pendingNodeDelete;
    setPendingNodeDelete(null);
    if (!pending) return;
    const ids = new Set(pending.nodes);
    const edgeIds = new Set(pending.edges);
    setDesign((d) => ({
      ...d,
      tables: d.tables.filter((t) => !ids.has(t.id)),
      relations: d.relations.filter((r) => !ids.has(r.from) && !ids.has(r.to) && !edgeIds.has(r.id)),
      shapes: (d.shapes ?? []).filter((s) => !ids.has(s.id)),
    }));
    setSelectedId(null);
    setSelectedShapeId(null);
    setSelectedEdgeId(null);
  };

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


  const updateTable = (id: string, patch: Partial<DesignTable>) =>
    setDesign((d) => ({ ...d, tables: d.tables.map((tb) => (tb.id === id ? { ...tb, ...patch } : tb)) }));

  const addShape = (kind: "rect" | "text") => {
    const id = newId("shape");
    const center = rfRef.current?.screenToFlowPosition?.({
      x: window.innerWidth / 2,
      y: window.innerHeight / 2,
    });
    const shape: ErdShape =
      kind === "rect"
        ? { id, kind, x: center?.x ?? 80, y: center?.y ?? 80, width: 320, height: 220, color: "muted", text: "" }
        : { id, kind, x: center?.x ?? 80, y: center?.y ?? 80, width: 180, height: 36, color: "muted", text: t("erdShape.memo") };
    setDesign((d) => ({ ...d, shapes: [...(d.shapes ?? []), shape] }));
    setSelectedShapeId(id);
    setSelectedId(null);
    setSelectedEdgeId(null);
  };

  const updateShape = (id: string, patch: Partial<ErdShape>) =>
    setDesign((d) => ({ ...d, shapes: (d.shapes ?? []).map((s) => (s.id === id ? { ...s, ...patch } : s)) }));

  const deleteShape = (id: string) => {
    setDesign((d) => ({ ...d, shapes: (d.shapes ?? []).filter((s) => s.id !== id) }));
    setSelectedShapeId(null);
  };

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

  const [showValidation, setShowValidation] = useState(false);
  const issues = useMemo(() => validateErd(design), [design]);
  const warnCount = issues.filter((i) => i.severity === "warning").length;

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
    // Fit the image tightly to the node bounding box (no wasted whitespace) and
    // render crisp. `scale` enlarges small/medium ERDs for sharp text but is
    // clamped so even a 300-table diagram stays under the browser canvas limit.
    const pad = 48;
    const bounds = getNodesBounds(rfNodes);
    const MAX = 12000; // max output dimension (px) — safe canvas size
    const fit = Math.min(
      (MAX - pad * 2) / Math.max(bounds.width, 1),
      (MAX - pad * 2) / Math.max(bounds.height, 1),
    );
    const scale = Math.max(0.2, Math.min(2, fit));
    const imgW = Math.ceil(bounds.width * scale + pad * 2);
    const imgH = Math.ceil(bounds.height * scale + pad * 2);
    const tx = pad - bounds.x * scale;
    const ty = pad - bounds.y * scale;
    // Match the canvas background (`bg-bg` = --bg-base, an "r g b" triple) so the
    // export looks right in dark mode instead of a jarring white.
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg-base").trim();
    try {
      const url = await toPng(el, {
        backgroundColor: bg ? `rgb(${bg})` : "#ffffff",
        width: imgW,
        height: imgH,
        // Extra DPI only when we didn't already enlarge (keeps small ERDs sharp
        // without blowing past the canvas limit on big ones).
        pixelRatio: scale >= 1.5 ? 1 : 2,
        style: {
          width: `${imgW}px`,
          height: `${imgH}px`,
          transformOrigin: "0 0",
          transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
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
    if (kind === "sql") {
      setSql(toSql(design, dialect));
      return;
    }
    if (kind === "image") {
      void onExportPng();
      return;
    }
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
    } else if (kind === "zip") {
      // All deliverables in one bundle (handoff): 5 docs + DDL for the dialect.
      const today = new Date().toLocaleDateString();
      const files: Record<string, Uint8Array> = {
        [`${base}_컬럼정의서.csv`]: strToU8(columnDictionaryCsv(design)),
        [`${base}_테이블정의서.csv`]: strToU8(tableDefinitionCsv(design)),
        [`${base}_매핑정의서.csv`]: strToU8(mappingSpecCsv(design)),
        [`${base}_제약인덱스정의서.csv`]: strToU8(constraintSpecCsv(design)),
        [`${base}_데이터정의서.md`]: strToU8(fullSpecMarkdown(design, docName || "ERD", today)),
        [`${base}_${dialect}.sql`]: strToU8(toSql(design, dialect)),
      };
      const zipped = zipSync(files);
      const blob = new Blob([zipped], { type: "application/zip" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${base}_정의서.zip`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(t("erdDocs.zipGenerated"));
      return;
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
      // If DA# diagram positions were reliably recovered, keep them; otherwise
      // fall back to auto-layout.
      const positioned = (incoming as ErdDesign & { __damxPositioned?: boolean }).__damxPositioned;
      // Positioned import: keep DA# layout but separate any overlapping boxes.
      setDesign((d) =>
        positioned ? removeOverlaps(mergeDesign(d, incoming)) : autoLayout(mergeDesign(d, incoming)),
      );
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
    // Shapes use their own panel — don't show the table context menu on them.
    if (node.type === "shape") {
      setSelectedShapeId(node.id);
      setSelectedId(null);
      setSelectedEdgeId(null);
      setMenu(null);
      return;
    }
    setSelectedId(node.id);
    setSelectedShapeId(null);
    setMenu({ x: e.clientX, y: e.clientY, kind: "node", nodeId: node.id });
  }, []);

  const selected = design.tables.find((tb) => tb.id === selectedId) ?? null;
  const selectedEdge = design.relations.find((r) => r.id === selectedEdgeId) ?? null;
  const selectedShape = (design.shapes ?? []).find((s) => s.id === selectedShapeId) ?? null;
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
        <select
          value=""
          onChange={(e) => {
            const v = e.target.value;
            e.target.value = "";
            if (v === "rect" || v === "text") addShape(v);
          }}
          className="h-8 rounded-md border border-border-subtle bg-bg px-2 text-xs text-text"
          aria-label={t("erdShape.add")}
          title={t("erdShape.add")}
        >
          <option value="">{t("erdShape.add")}</option>
          <option value="rect">{t("erdShape.rect")}</option>
          <option value="text">{t("erdShape.text")}</option>
        </select>
        <select
          value=""
          onChange={(e) => {
            const v = e.target.value;
            e.target.value = "";
            if (v === "connection") setShowImport(true);
            else if (v === "damx") damxRef.current?.click();
            else if (v === "ddl") setShowDdl(true);
          }}
          className="h-8 rounded-md border border-border-subtle bg-bg px-2 text-xs text-text"
          aria-label={t("erdDesign.importMenu")}
          title={t("erdDesign.connectHint")}
        >
          <option value="">{t("erdDesign.importMenu")}</option>
          <option value="connection" disabled={!ws?.id}>
            {t("erdDesign.import")}
          </option>
          <option value="damx">{t("erdDesign.importDamx")}</option>
          <option value="ddl">{t("erdDesign.importDdl")}</option>
        </select>
        <input
          ref={damxRef}
          type="file"
          accept=".damx"
          className="hidden"
          onChange={(e) => void onDamxFile(e)}
        />
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
            variant={warnCount > 0 ? "secondary" : "ghost"}
            onClick={() => setShowValidation((v) => !v)}
            disabled={design.tables.length === 0}
            className={warnCount > 0 ? "!text-warning" : undefined}
          >
            <AlertTriangleIcon size={14} />
            {warnCount > 0 ? t("erdValidate.buttonCount", { n: warnCount }) : t("erdValidate.button")}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => onAutoLayout()}
            disabled={design.tables.length === 0}
          >
            <LayoutGridIcon size={14} />
            {t("erdDesign.autoLayout")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setDesign((d) => removeOverlaps(d));
              setTimeout(() => rfRef.current?.fitView({ padding: 0.2, duration: 300 }), 60);
            }}
            disabled={design.tables.length === 0}
            title={t("erdDesign.declutterHint")}
          >
            {t("erdDesign.declutter")}
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
          <select
            value={nameMode}
            onChange={(e) => setNameMode(e.target.value as NameMode)}
            disabled={design.tables.length === 0}
            className="h-8 rounded-md border border-border-subtle bg-bg px-1 text-xs text-text"
            aria-label={t("erdDesign.nameMode")}
            title={t("erdDesign.nameMode")}
          >
            <option value="physical">{t("erdDesign.namePhysical")}</option>
            <option value="logical">{t("erdDesign.nameLogical")}</option>
            <option value="both">{t("erdDesign.nameBoth")}</option>
          </select>
          <select
            value={String(design.fontScale ?? 1)}
            onChange={(e) => setDesign((d) => ({ ...d, fontScale: Number(e.target.value) }))}
            disabled={design.tables.length === 0}
            className="h-8 rounded-md border border-border-subtle bg-bg px-1 text-xs text-text"
            aria-label={t("erdDesign.fontSize")}
            title={t("erdDesign.fontSize")}
          >
            <option value="0.85">A-</option>
            <option value="1">A</option>
            <option value="1.2">A+</option>
            <option value="1.4">A++</option>
          </select>
          <select
            value=""
            onChange={(e) => {
              onGenerateDoc(e.target.value);
              e.target.value = "";
            }}
            disabled={design.tables.length === 0}
            className="h-8 rounded-md border border-border-subtle bg-bg px-2 text-xs text-text"
            aria-label={t("erdDesign.exportMenu")}
            title={t("erdDesign.exportMenu")}
          >
            <option value="">{t("erdDesign.exportMenu")}</option>
            <option value="sql">{t("erdDesign.exportSql")}</option>
            <option value="image">{t("erdDesign.exportImage")}</option>
            <option value="columns">{t("erdDocs.columns")}</option>
            <option value="tables">{t("erdDocs.tables")}</option>
            <option value="mapping">{t("erdDocs.mapping")}</option>
            <option value="constraints">{t("erdDocs.constraints")}</option>
            <option value="markdown">{t("erdDocs.markdown")}</option>
            <option value="zip">{t("erdDocs.zip")}</option>
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
          {showValidation ? (
            <div className="absolute right-3 top-3 z-20 flex max-h-[70%] w-72 flex-col rounded-lg border border-border-subtle bg-surface shadow-lg">
              <div className="flex items-center justify-between border-b border-border-subtle px-3 py-2">
                <span className="text-xs font-semibold text-text">{t("erdValidate.title")}</span>
                <button
                  onClick={() => setShowValidation(false)}
                  aria-label={t("common.close")}
                  className="text-text-muted hover:text-text"
                >
                  <XIcon size={14} />
                </button>
              </div>
              {issues.length === 0 ? (
                <div className="flex items-center gap-2 px-3 py-4 text-sm text-text-secondary">
                  <CheckCircle2Icon size={16} className="text-success" />
                  {t("erdValidate.clean")}
                </div>
              ) : (
                <ul className="overflow-y-auto py-1">
                  {issues.map((iss, n) => (
                    <li key={n}>
                      <button
                        onClick={() => onJumpToTable(iss.tableName)}
                        className="flex w-full items-start gap-2 px-3 py-1.5 text-left text-xs hover:bg-overlay"
                      >
                        <AlertTriangleIcon
                          size={13}
                          className={`mt-0.5 shrink-0 ${iss.severity === "warning" ? "text-warning" : "text-text-muted"}`}
                        />
                        <span className="min-w-0">
                          <span className="font-mono text-text">{iss.tableName}</span>
                          <span className="text-text-secondary">
                            {" — "}
                            {t(`erdValidate.${iss.kind}` as keyof Messages, { col: iss.column ?? "" })}
                          </span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
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
              nodeTypes={NODE_TYPES}
              elevateNodesOnSelect={false}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodesDelete={onNodesDelete}
              onBeforeDelete={onBeforeDelete}
              onEdgesDelete={onEdgesDelete}
              deleteKeyCode={["Delete", "Backspace"]}
              onConnect={onConnect}
              onNodeDragStop={onNodeDragStop}
              onNodeClick={onNodeClick}
              onEdgeClick={(_e, edge) => {
                setSelectedEdgeId(edge.id);
                setSelectedId(null);
                setSelectedShapeId(null);
              }}
              onPaneClick={() => {
                setSelectedId(null);
                setSelectedEdgeId(null);
                setSelectedShapeId(null);
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
              setPendingNodeDelete({ nodes: [selected.id], edges: [] });
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
              setPendingNodeDelete({ nodes: [], edges: [selectedEdge.id] });
              setSelectedEdgeId(null);
            }}
            onClose={() => setSelectedEdgeId(null)}
          />
        ) : selectedShape ? (
          <ShapePanel
            shape={selectedShape}
            t={t}
            onChange={(patch) => updateShape(selectedShape.id, patch)}
            onDelete={() => deleteShape(selectedShape.id)}
            onClose={() => setSelectedShapeId(null)}
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

      <ConfirmDialog
        open={pendingNodeDelete !== null}
        title={t("erdDesign.confirmDeleteTitle", { n: (pendingNodeDelete?.nodes.length ?? 0) + (pendingNodeDelete?.edges.length ?? 0) })}
        description={t("erdDesign.confirmDeleteDesc")}
        confirmLabel={t("erdDesign.deleteTable")}
        destructive
        onConfirm={confirmNodeDelete}
        onCancel={() => setPendingNodeDelete(null)}
      />
      {showImport && ws?.id ? (
        <ImportTablesDialog
          workspaceId={ws.id}
          onClose={() => setShowImport(false)}
          onImport={(incoming) => setDesign((d) => mergeDesign(d, incoming))}
        />
      ) : null}

      <ImportDdlDialog
        open={showDdl}
        onClose={() => setShowDdl(false)}
        onImport={(incoming) => {
          setDesign((d) => autoLayout(mergeDesign(d, incoming)));
          setShowDdl(false);
          setTimeout(() => rfRef.current?.fitView({ padding: 0.2, duration: 300 }), 60);
          toast.success(t("erdDesign.damxImported", { n: incoming.tables.length }));
        }}
      />

      {menu ? (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setMenu(null)} />
          <div
            className="fixed z-50 min-w-36 rounded-md border border-border-subtle bg-elevated py-1 text-sm shadow-lg"
            style={{ left: menu.x, top: menu.y }}
          >
            {menu.kind === "pane" ? (
              <>
                <button
                  className="block w-full px-3 py-1.5 text-left text-text hover:bg-overlay"
                  onClick={() => {
                    onAddTable();
                    setMenu(null);
                  }}
                >
                  {t("erdDesign.addTable")}
                </button>
                <button
                  className="block w-full px-3 py-1.5 text-left text-text hover:bg-overlay"
                  onClick={() => {
                    addShape("rect");
                    setMenu(null);
                  }}
                >
                  {t("erdShape.rect")}
                </button>
                <button
                  className="block w-full px-3 py-1.5 text-left text-text hover:bg-overlay"
                  onClick={() => {
                    addShape("text");
                    setMenu(null);
                  }}
                >
                  {t("erdShape.text")}
                </button>
              </>
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
                    setPendingNodeDelete({ nodes: [menu.nodeId], edges: [] });
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
