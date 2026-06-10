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
  Handle,
  NodeResizeControl,
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
  ArrowRightLeftIcon,
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
import { VerifyDbDialog } from "@/components/erd/verify-db-dialog";
import { CreateMigrationDialog } from "@/components/erd/create-migration-dialog";
import { ImportDdlDialog } from "@/components/erd/import-ddl-dialog";
import { parseDamxWithAreas } from "@/lib/damx";
import { autoLayout, layoutAreas, removeOverlaps } from "@/lib/erd-layout";
import { validateErd } from "@/lib/erd-validate";
import { exportErdExcel } from "@/lib/erd-excel";
import {
  columnDictionaryCsv,
  constraintSpecCsv,
  fullSpecMarkdown,
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

// Vertica quotes identifiers with double quotes (postgres-style), so toSql
// needs no dialect-specific branch for it.
const DIALECTS = ["postgres", "mysql", "sqlite", "vertica", "snowflake", "bigquery"];

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

/** Table node (Phase AKT/AKU): the label JSX plus resize grips when selected
 *  — right edge = width, bottom edge = height, corner = both. Sizes persist
 *  on the design (DesignTable.w/h). Includes the source/target Handles the
 *  default node type used to provide — without them React Flow drops every
 *  edge touching the node (the "lines all vanished" regression).
 */
const GRIP: React.CSSProperties = {
  position: "absolute",
  borderRadius: 2,
  background: "rgb(var(--accent) / 0.55)",
};
function TableNode({ data, selected }: NodeProps) {
  const d = data as {
    label: React.ReactNode;
    onWidth?: (px: number) => void;
    onHeight?: (px: number) => void;
    onBoth?: (w: number, h: number) => void;
  };
  return (
    <>
      {/* Visible like the default node's dots — they're the drag-FK affordance. */}
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      {selected ? (
        <>
          <NodeResizeControl
            position="right"
            minWidth={160}
            maxWidth={640}
            onResizeEnd={(_e, p) => d.onWidth?.(p.width)}
            style={{ background: "transparent", border: "none" }}
          >
            <div style={{ ...GRIP, right: 2, top: "50%", transform: "translateY(-50%)", width: 4, height: 30, cursor: "ew-resize" }} />
          </NodeResizeControl>
          <NodeResizeControl
            position="bottom"
            minHeight={56}
            maxHeight={1400}
            onResizeEnd={(_e, p) => d.onHeight?.(p.height)}
            style={{ background: "transparent", border: "none" }}
          >
            <div style={{ ...GRIP, bottom: 2, left: "50%", transform: "translateX(-50%)", height: 4, width: 30, cursor: "ns-resize" }} />
          </NodeResizeControl>
          <NodeResizeControl
            position="bottom-right"
            minWidth={160}
            maxWidth={640}
            minHeight={56}
            maxHeight={1400}
            onResizeEnd={(_e, p) => d.onBoth?.(p.width, p.height)}
            style={{ background: "transparent", border: "none" }}
          >
            <div style={{ ...GRIP, right: 1, bottom: 1, width: 9, height: 9, cursor: "nwse-resize" }} />
          </NodeResizeControl>
        </>
      ) : null}
      {d.label}
    </>
  );
}

const NODE_TYPES: NodeTypes = { shape: ShapeNode, table: TableNode };

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
  // SQL export: emit table/column comments from logical names (Phase AKQ).
  const [sqlComments, setSqlComments] = useState(true);
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

  // Subject-area tabs (주제영역, Phase AKH): null = the whole model ("전체").
  // An active area filters the canvas to its member tables and uses the
  // area's own positions, like DA#'s diagram panes.
  const [activeAreaId, setActiveAreaId] = useState<string | null>(null);
  const activeArea = useMemo(
    () => (activeAreaId ? (design.areas ?? []).find((a) => a.id === activeAreaId) ?? null : null),
    [design.areas, activeAreaId],
  );
  const [renamingAreaId, setRenamingAreaId] = useState<string | null>(null);
  const [areaRenameVal, setAreaRenameVal] = useState("");
  const [pendingAreaDelete, setPendingAreaDelete] = useState<string | null>(null);
  // No "전체" tab (user feedback): when areas exist the FIRST tab is the default
  // view; with no areas the diagram is a plain single canvas.
  useEffect(() => {
    const areas = design.areas ?? [];
    if (areas.length === 0) {
      if (activeAreaId !== null) setActiveAreaId(null);
      return;
    }
    if (!activeAreaId || !areas.some((a) => a.id === activeAreaId)) {
      setActiveAreaId(areas[0].id);
    }
  }, [design.areas, activeAreaId]);
  const onAddArea = () => {
    const id = newId("area");
    setDesign((d) => ({
      ...d,
      areas: [
        ...(d.areas ?? []),
        { id, name: t("erdDesign.areaDefaultName", { n: (d.areas?.length ?? 0) + 1 }), tableIds: [] },
      ],
    }));
    setActiveAreaId(id);
  };
  const commitAreaRename = (id: string) => {
    const name = areaRenameVal.trim();
    setRenamingAreaId(null);
    if (!name) return;
    setDesign((d) => ({
      ...d,
      areas: (d.areas ?? []).map((a) => (a.id === id ? { ...a, name } : a)),
    }));
  };
  const confirmAreaDelete = () => {
    const id = pendingAreaDelete;
    setPendingAreaDelete(null);
    if (!id) return;
    setDesign((d) => {
      const areas = (d.areas ?? []).filter((a) => a.id !== id);
      // Tables exclusive to the removed tab would become invisible orphans
      // (no 전체 view) — delete them from the model too.
      if (areas.length > 0) {
        const shown = new Set(areas.flatMap((a) => a.tableIds));
        const orphan = new Set(d.tables.filter((t) => !shown.has(t.id)).map((t) => t.id));
        return {
          ...d,
          tables: d.tables.filter((t) => !orphan.has(t.id)),
          relations: d.relations.filter((r) => !orphan.has(r.from) && !orphan.has(r.to)),
          areas,
        };
      }
      // Last tab removed → back to a plain single canvas keeping everything.
      return { ...d, areas };
    });
    if (activeAreaId === id) setActiveAreaId(null);
  };
  // Tables that exist ONLY on the tab pending removal (deleted with it).
  const pendingAreaExclusive = useMemo(() => {
    if (!pendingAreaDelete) return 0;
    const others = new Set(
      (design.areas ?? []).filter((a) => a.id !== pendingAreaDelete).flatMap((a) => a.tableIds),
    );
    const target = (design.areas ?? []).find((a) => a.id === pendingAreaDelete);
    if (!target) return 0;
    if ((design.areas ?? []).length <= 1) return 0; // last tab → nothing deleted
    return target.tableIds.filter((tid) => !others.has(tid)).length;
  }, [design.areas, pendingAreaDelete]);

  // Persist a manually-dragged edge bend (Phase AKZ); undefined = back to auto.
  const setEdgeCenterRatio = useCallback((edgeId: string, ratio: number | undefined) => {
    setDesign((d) => ({
      ...d,
      relations: d.relations.map((r) => (r.id === edgeId ? { ...r, centerRatio: ratio } : r)),
    }));
  }, []);

  // Persist a user-resized node size (px at fontScale 1). Width and height
  // persist independently so a width tweak doesn't freeze the auto height.
  const setTableSize = useCallback((id: string, patch: { w?: number; h?: number }) => {
    setDesign((d) => ({
      ...d,
      tables: d.tables.map((tb) =>
        tb.id === id
          ? {
              ...tb,
              ...(patch.w !== undefined ? { w: Math.round(patch.w) } : {}),
              ...(patch.h !== undefined ? { h: Math.round(patch.h) } : {}),
            }
          : tb,
      ),
    }));
  }, []);

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
    const memberIds = activeArea ? new Set(activeArea.tableIds) : null;
    const visible = memberIds ? design.tables.filter((t) => memberIds.has(t.id)) : design.tables;
    return visible.map((tb) => ({
      id: tb.id,
      type: "table",
      position: activeArea?.positions?.[tb.id] ?? { x: tb.x, y: tb.y },
      data: {
        label: nodeLabel(tb, fkByTable.get(tb.id) ?? new Set(), nameMode, scale),
        onWidth: (px: number) => setTableSize(tb.id, { w: px / scale }),
        onHeight: (px: number) => setTableSize(tb.id, { h: px / scale }),
        onBoth: (w: number, h: number) => setTableSize(tb.id, { w: w / scale, h: h / scale }),
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      style: {
        width: Math.round((tb.w ?? 240) * scale),
        ...(tb.h ? { height: Math.round(tb.h * scale), overflow: "hidden" } : {}),
        padding: 0,
        borderRadius: 8,
        background: "rgb(var(--bg-elevated))",
        color: "rgb(var(--text))",
      },
    }));
  }, [design, nameMode, activeArea, setTableSize]);

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

  const edges = useMemo<Edge[]>(() => {
    const memberIds = activeArea ? new Set(activeArea.tableIds) : null;
    const rels = memberIds
      ? design.relations.filter((r) => memberIds.has(r.from) && memberIds.has(r.to))
      : design.relations;
    return rels.map((r) => ({
        id: r.id,
        source: r.from,
        target: r.to,
        label: r.fromColumn,
        type: "crowsfoot",
        data: {
          sourceCard: r.sourceCard ?? "many",
          targetCard: r.targetCard ?? "one",
          centerRatio: r.centerRatio,
          onCenterRatio: setEdgeCenterRatio,
        },
        style: {
          stroke: "rgb(var(--accent))",
          strokeWidth: r.id === selectedEdgeId ? 2.5 : 1.5,
        },
        labelStyle: { fontSize: 10, fill: "rgb(var(--text-muted))" },
      }));
  }, [design, selectedEdgeId, activeArea, setEdgeCenterRatio]);

  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(nodes);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState(edges);
  useEffect(() => setRfNodes(nodes), [nodes, setRfNodes]);
  useEffect(() => setRfEdges(edges), [edges, setRfEdges]);

  const onConnect = useCallback((c: Connection) => {
    if (c.source && c.target) setDesign((d) => connect(d, c.source!, c.target!));
  }, []);

  const onNodeDragStop = useCallback(
    (_e: unknown, node: Node) => {
      setDesign((d) => {
        if (node.type === "shape") {
          return {
            ...d,
            shapes: (d.shapes ?? []).map((s) =>
              s.id === node.id ? { ...s, x: node.position.x, y: node.position.y } : s,
            ),
          };
        }
        // On a subject-area tab, dragging moves the table on THIS tab only.
        if (activeAreaId) {
          return {
            ...d,
            areas: (d.areas ?? []).map((a) =>
              a.id === activeAreaId
                ? {
                    ...a,
                    positions: {
                      ...(a.positions ?? {}),
                      [node.id]: { x: node.position.x, y: node.position.y },
                    },
                  }
                : a,
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
    },
    [activeAreaId],
  );

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
    setDesign((d) => {
      // On a subject-area tab: remove from THIS tab; a table that no longer
      // appears on ANY tab is deleted from the model too (there is no "전체"
      // tab, so keeping invisible orphans would only confuse).
      if (activeAreaId) {
        const areas = (d.areas ?? []).map((a) =>
          a.id === activeAreaId
            ? {
                ...a,
                tableIds: a.tableIds.filter((tid) => !ids.has(tid)),
                positions: Object.fromEntries(
                  Object.entries(a.positions ?? {}).filter(([tid]) => !ids.has(tid)),
                ),
              }
            : a,
        );
        const stillShown = new Set(areas.flatMap((a) => a.tableIds));
        const gone = new Set([...ids].filter((tid) => !stillShown.has(tid)));
        return {
          ...d,
          tables: d.tables.filter((t) => !gone.has(t.id)),
          relations: d.relations.filter((r) => !gone.has(r.from) && !gone.has(r.to) && !edgeIds.has(r.id)),
          areas,
          shapes: (d.shapes ?? []).filter((s) => !ids.has(s.id)),
        };
      }
      return {
        ...d,
        tables: d.tables.filter((t) => !ids.has(t.id)),
        relations: d.relations.filter((r) => !ids.has(r.from) && !ids.has(r.to) && !edgeIds.has(r.id)),
        shapes: (d.shapes ?? []).filter((s) => !ids.has(s.id)),
        areas: (d.areas ?? []).map((a) => ({
          ...a,
          tableIds: a.tableIds.filter((tid) => !ids.has(tid)),
        })),
      };
    });
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
  const [showDbVerify, setShowDbVerify] = useState(false);
  const [migrateTableIds, setMigrateTableIds] = useState<string[] | null>(null);
  const issues = useMemo(() => validateErd(design), [design]);
  const warnCount = issues.filter((i) => i.severity === "warning").length;

  const rfRef = useRef<ReactFlowInstance<Node, Edge> | null>(null);
  const [layoutDir, setLayoutDir] = useState<"TB" | "LR">("TB");
  // Run a layout function over the visible canvas: the whole model, or — when
  // a subject-area tab is active — just that tab (writing the tab's positions).
  const applyLayout = useCallback(
    (fn: (d: ErdDesign) => ErdDesign) => {
      setDesign((d) => {
        if (!activeAreaId) return fn(d);
        const area = (d.areas ?? []).find((a) => a.id === activeAreaId);
        if (!area) return fn(d);
        const ids = new Set(area.tableIds);
        const sub: ErdDesign = {
          tables: d.tables
            .filter((t) => ids.has(t.id))
            .map((t) => ({ ...t, x: area.positions?.[t.id]?.x ?? t.x, y: area.positions?.[t.id]?.y ?? t.y })),
          relations: d.relations.filter((r) => ids.has(r.from) && ids.has(r.to)),
        };
        const laid = fn(sub);
        const positions: Record<string, { x: number; y: number }> = {};
        for (const t of laid.tables) positions[t.id] = { x: t.x, y: t.y };
        // Node sizes are a table property (not per-tab) — apply any size the
        // layout chose (AKV visibility sizing) globally.
        const sizeBy = new Map(laid.tables.map((tb) => [tb.id, { w: tb.w, h: tb.h }]));
        return {
          ...d,
          tables: d.tables.map((tb) => {
            const s = sizeBy.get(tb.id);
            return s ? { ...tb, w: s.w, h: s.h } : tb;
          }),
          areas: (d.areas ?? []).map((a) => (a.id === activeAreaId ? { ...a, positions } : a)),
        };
      });
      setTimeout(() => rfRef.current?.fitView({ padding: 0.2, duration: 300 }), 60);
    },
    [activeAreaId],
  );

  const onAutoLayout = (dir: "TB" | "LR" = layoutDir) => {
    if (design.tables.length === 0) return;
    setLayoutDir(dir);
    // Fresh layout = fresh routing: drop manual bends so lines re-optimise.
    setDesign((d) => ({
      ...d,
      relations: d.relations.map((r) => (r.centerRatio !== undefined ? { ...r, centerRatio: undefined } : r)),
    }));
    applyLayout((d) => autoLayout(d, dir));
  };

  // PNG rendering of a big diagram can take many seconds — show a blocking
  // "generating image" overlay so it doesn't look frozen.
  const [exportingPng, setExportingPng] = useState(false);
  const onExportPng = async () => {
    if (design.tables.length === 0 || exportingPng) return;
    const el = document.querySelector<HTMLElement>(".react-flow__viewport");
    if (!el) return;
    setExportingPng(true);
    // Let the overlay paint before toPng() hogs the main thread.
    await new Promise((r) => setTimeout(r, 50));
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
    } finally {
      setExportingPng(false);
    }
  };

  const onGenerateDoc = (kind: string) => {
    if (design.tables.length === 0) return;
    if (kind === "sql") {
      setSql(toSql(design, dialect, { comments: sqlComments }));
      return;
    }
    if (kind === "image") {
      void onExportPng();
      return;
    }
    const base = (docName || "erd").replace(/[/\\?%*:|"<>]/g, "_");
    if (kind === "excel") {
      // Styled multi-sheet workbook; ExcelJS loads lazily on first use.
      void (async () => {
        try {
          const blob = await exportErdExcel(design, docName || "ERD");
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `${base}_데이터정의서.xlsx`;
          a.click();
          URL.revokeObjectURL(url);
          toast.success(t("erdDocs.excelGenerated"));
        } catch {
          toast.error(t("erdDocs.excelError"));
        }
      })();
      return;
    }
    let content = "";
    let filename = "";
    let mime = "text/csv;charset=utf-8";
    if (kind === "columns") {
      content = columnDictionaryCsv(design);
      filename = `${base}_컬럼정의서.csv`;
    } else if (kind === "tables") {
      content = tableDefinitionCsv(design);
      filename = `${base}_테이블정의서.csv`;
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
        [`${base}_제약인덱스정의서.csv`]: strToU8(constraintSpecCsv(design)),
        [`${base}_데이터정의서.md`]: strToU8(fullSpecMarkdown(design, docName || "ERD", today)),
        [`${base}_${dialect}.sql`]: strToU8(toSql(design, dialect, { comments: sqlComments })),
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

  // Search (Phase AKK): substring match (%str% semantics) over physical AND
  // logical names, with a styled autocomplete dropdown. Jumping is tab-aware —
  // it switches to a tab containing the table and uses that tab's position.
  const [searchQ, setSearchQ] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchIdx, setSearchIdx] = useState(0);
  const searchMatches = useMemo(() => {
    const q = searchQ.replace(/%/g, "").trim().toLowerCase();
    if (!q) return [];
    const areaOf = (id: string) => (design.areas ?? []).find((a) => a.tableIds.includes(id));
    return design.tables
      .filter(
        (tb) => tb.name.toLowerCase().includes(q) || (tb.logical ?? "").toLowerCase().includes(q),
      )
      .slice(0, 12)
      .map((tb) => ({ tb, area: areaOf(tb.id) }));
  }, [searchQ, design.tables, design.areas]);

  const jumpToTable = (tb: DesignTable) => {
    setSearchOpen(false);
    setSearchQ("");
    setSelectedId(tb.id);
    setSelectedEdgeId(null);
    // Tab-aware: stay if the active tab shows it; otherwise switch to the
    // first tab containing it (or plain canvas when there are no tabs).
    let area = activeArea && activeArea.tableIds.includes(tb.id) ? activeArea : null;
    if (!area && (design.areas ?? []).length > 0) {
      area = (design.areas ?? []).find((a) => a.tableIds.includes(tb.id)) ?? null;
      if (area) setActiveAreaId(area.id);
    }
    const pos = area?.positions?.[tb.id] ?? { x: tb.x, y: tb.y };
    setTimeout(() => rfRef.current?.setCenter(pos.x + 110, pos.y + 70, { zoom: 1.1, duration: 400 }), 80);
  };

  const damxRef = useRef<HTMLInputElement>(null);
  const onDamxFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const incoming = parseDamxWithAreas(await file.arrayBuffer());
      if (incoming.tables.length === 0) {
        toast.error(t("erdDesign.damxEmpty"));
        return;
      }
      // If DA# diagram positions were reliably recovered, keep them; otherwise
      // fall back to auto-layout. Subject areas come in as tabs (Phase AKH).
      const positioned = (incoming as ErdDesign & { __damxPositioned?: boolean }).__damxPositioned;
      setDesign((d) => {
        const merged = mergeDesign(d, incoming);
        const laid = positioned ? removeOverlaps(merged) : autoLayout(merged);
        // mergeDesign dedupes same-named tables (keeping the existing id), so
        // remap the incoming areas' memberships/positions onto the final ids
        // and APPEND to any tabs the diagram already had.
        const nameByIncomingId = new Map(incoming.tables.map((tb) => [tb.id, tb.name]));
        const idByName = new Map(laid.tables.map((tb) => [tb.name, tb.id]));
        const remap = (id: string) => idByName.get(nameByIncomingId.get(id) ?? "") ?? null;
        const incomingAreas = (incoming.areas ?? []).map((a) => {
          const tableIds = a.tableIds.map(remap).filter((x): x is string => !!x);
          const positions: Record<string, { x: number; y: number }> = {};
          for (const [oldId, pos] of Object.entries(a.positions ?? {})) {
            const nid = remap(oldId);
            if (nid) positions[nid] = pos;
          }
          return { ...a, tableIds, positions };
        });
        return layoutAreas({ ...laid, areas: [...(d.areas ?? []), ...incomingAreas] });
      });
      setActiveAreaId(null);
      setTimeout(() => rfRef.current?.fitView({ padding: 0.2, duration: 300 }), 60);
      toast.success(t("erdDesign.damxImported", { n: incoming.tables.length }));
    } catch {
      toast.error(t("erdDesign.damxError"));
    }
  };

  const onAddTable = () =>
    setDesign((d) => {
      const n = d.tables.length;
      const next = addTable(d, `table_${n + 1}`, 60 + (n % 4) * 280, 60 + Math.floor(n / 4) * 220);
      // A table created while a subject-area tab is active joins that tab.
      if (activeAreaId) {
        const created = next.tables[next.tables.length - 1];
        return {
          ...next,
          areas: (next.areas ?? []).map((a) =>
            a.id === activeAreaId ? { ...a, tableIds: [...a.tableIds, created.id] } : a,
          ),
        };
      }
      return next;
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
          <div className="relative">
            <input
              value={searchQ}
              placeholder={t("erdDesign.searchTable")}
              className="h-8 w-52 rounded-md border border-border-subtle bg-bg px-2 text-xs text-text focus-visible:border-accent focus-visible:outline-none"
              onChange={(e) => {
                setSearchQ(e.target.value);
                setSearchOpen(true);
                setSearchIdx(0);
              }}
              onFocus={() => setSearchOpen(true)}
              onBlur={() => setTimeout(() => setSearchOpen(false), 120)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setSearchIdx((i) => Math.min(i + 1, searchMatches.length - 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setSearchIdx((i) => Math.max(i - 1, 0));
                } else if (e.key === "Enter") {
                  const m = searchMatches[searchIdx];
                  if (m) jumpToTable(m.tb);
                } else if (e.key === "Escape") {
                  setSearchOpen(false);
                }
              }}
            />
            {searchOpen && searchQ.trim() ? (
              <div className="absolute left-0 top-9 z-30 max-h-72 w-72 overflow-y-auto rounded-md border border-border-subtle bg-elevated shadow-lg">
                {searchMatches.length === 0 ? (
                  <div className="px-3 py-2 text-xs text-text-muted">{t("common.noResults")}</div>
                ) : (
                  searchMatches.map((m, i) => (
                    <button
                      key={m.tb.id}
                      type="button"
                      onMouseDown={(e) => {
                        e.preventDefault();
                        jumpToTable(m.tb);
                      }}
                      onMouseEnter={() => setSearchIdx(i)}
                      className={`flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left ${
                        i === searchIdx ? "bg-overlay" : ""
                      }`}
                    >
                      <Table2Icon size={12} className="shrink-0 text-accent" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-mono text-xs text-text">{m.tb.name}</span>
                        {m.tb.logical && m.tb.logical !== m.tb.name ? (
                          <span className="block truncate text-[11px] text-text-muted">{m.tb.logical}</span>
                        ) : null}
                      </span>
                      {m.area ? (
                        <span className="shrink-0 rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">
                          {m.area.name}
                        </span>
                      ) : null}
                    </button>
                  ))
                )}
              </div>
            ) : null}
          </div>
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
            variant="ghost"
            onClick={() => setShowDbVerify(true)}
            disabled={design.tables.length === 0 || !ws?.id}
            title={t("erdVerify.hint")}
          >
            <DatabaseIcon size={14} />
            {t("erdVerify.button")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setMigrateTableIds((activeArea?.tableIds ?? design.tables.map((tb) => tb.id)))}
            disabled={design.tables.length === 0 || !ws?.id}
            title={t("erdMigrate.hint")}
          >
            <ArrowRightLeftIcon size={14} />
            {t("erdMigrate.button")}
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
            onClick={() => applyLayout((d) => removeOverlaps(d))}
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
            <option value="constraints">{t("erdDocs.constraints")}</option>
            <option value="markdown">{t("erdDocs.markdown")}</option>
            <option value="excel">{t("erdDocs.excel")}</option>
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
          {(design.areas ?? []).length > 0 ? (
            <div className="absolute left-2 top-2 z-20 flex max-w-[calc(100%-1rem)] flex-wrap items-center gap-1 rounded-lg border border-border-subtle bg-surface/95 p-1 shadow-sm">
              {(design.areas ?? []).map((a) =>
                renamingAreaId === a.id ? (
                  <Input
                    key={a.id}
                    autoFocus
                    value={areaRenameVal}
                    onChange={(e) => setAreaRenameVal(e.target.value)}
                    onBlur={() => commitAreaRename(a.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                      if (e.key === "Escape") setRenamingAreaId(null);
                    }}
                    className="h-6 w-32 text-xs"
                  />
                ) : (
                  <button
                    key={a.id}
                    type="button"
                    onClick={() => {
                      setActiveAreaId(a.id);
                      setTimeout(() => rfRef.current?.fitView({ padding: 0.2, duration: 300 }), 60);
                    }}
                    onDoubleClick={() => {
                      setAreaRenameVal(a.name);
                      setRenamingAreaId(a.id);
                    }}
                    title={t("erdDesign.areaTabHint")}
                    className={`group cursor-pointer rounded-md px-2 py-1 text-xs font-medium transition-colors ${
                      activeAreaId === a.id
                        ? "bg-accent/15 text-accent"
                        : "text-text-secondary hover:bg-overlay hover:text-text"
                    }`}
                  >
                    {a.name}
                    <span className="ml-1 text-[10px] opacity-70">{a.tableIds.length}</span>
                    {activeAreaId === a.id ? (
                      <XIcon
                        size={11}
                        className="ml-1 inline-block opacity-60 hover:opacity-100"
                        aria-label={t("erdDesign.areaDelete")}
                        onClick={(e) => {
                          e.stopPropagation();
                          setPendingAreaDelete(a.id);
                        }}
                      />
                    ) : null}
                  </button>
                ),
              )}
              <button
                type="button"
                onClick={onAddArea}
                title={t("erdDesign.areaAdd")}
                className="cursor-pointer rounded-md px-1.5 py-1 text-text-muted hover:bg-overlay hover:text-text"
              >
                <PlusIcon size={12} />
              </button>
            </div>
          ) : null}
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
                        onClick={() => {
                          const tb = design.tables.find((x) => x.name === iss.tableName);
                          if (tb) jumpToTable(tb);
                        }}
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
              <div className="flex items-center gap-3">
                <label className="flex cursor-pointer items-center gap-1.5 text-xs text-text-secondary">
                  <input
                    type="checkbox"
                    checked={sqlComments}
                    onChange={(e) => {
                      setSqlComments(e.target.checked);
                      setSql(toSql(design, dialect, { comments: e.target.checked }));
                    }}
                  />
                  {t("erdDesign.sqlComments")}
                </label>
                <button onClick={() => setSql(null)} aria-label={t("common.close")} className="text-text-muted hover:text-text">
                  <XIcon size={16} />
                </button>
              </div>
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
        title={t(activeAreaId ? "erdDesign.confirmRemoveFromAreaTitle" : "erdDesign.confirmDeleteTitle", {
          n: (pendingNodeDelete?.nodes.length ?? 0) + (pendingNodeDelete?.edges.length ?? 0),
        })}
        description={t(activeAreaId ? "erdDesign.confirmRemoveFromAreaDesc" : "erdDesign.confirmDeleteDesc")}
        confirmLabel={t(activeAreaId ? "erdDesign.removeFromArea" : "erdDesign.deleteTable")}
        destructive={!activeAreaId}
        onConfirm={confirmNodeDelete}
        onCancel={() => setPendingNodeDelete(null)}
      />
      <ConfirmDialog
        open={pendingAreaDelete !== null}
        title={t("erdDesign.areaDeleteTitle", {
          name: (design.areas ?? []).find((a) => a.id === pendingAreaDelete)?.name ?? "",
        })}
        description={
          pendingAreaExclusive > 0
            ? t("erdDesign.areaDeleteDescOrphans", { n: pendingAreaExclusive })
            : t("erdDesign.areaDeleteDesc")
        }
        confirmLabel={t("erdDesign.areaDelete")}
        destructive
        onConfirm={confirmAreaDelete}
        onCancel={() => setPendingAreaDelete(null)}
      />
      {exportingPng ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="flex items-center gap-3 rounded-lg border border-border-subtle bg-surface px-5 py-4 shadow-xl">
            <span className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-accent/30 border-t-accent" />
            <div>
              <p className="text-sm font-medium text-text">{t("erdDesign.exportingImage")}</p>
              <p className="text-xs text-text-muted">{t("erdDesign.exportingImageHint")}</p>
            </div>
          </div>
        </div>
      ) : null}
      {showDbVerify && ws?.id ? (
        <VerifyDbDialog workspaceId={ws.id} design={design} onClose={() => setShowDbVerify(false)} />
      ) : null}
      {migrateTableIds && ws?.id ? (
        <CreateMigrationDialog
          workspaceId={ws.id}
          slug={slug}
          design={design}
          initialTableIds={migrateTableIds}
          onClose={() => setMigrateTableIds(null)}
        />
      ) : null}
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
