"use client";

import { useCallback, useEffect, useState } from "react";
import { PlusIcon, XCircleIcon, XIcon } from "lucide-react";
import { Input } from "@/components/ui/input";
import {
  findOperator,
  getOperatorDescription,
  getOperatorLabel,
  type FieldDef,
  type OperatorSpec,
} from "@/lib/operators";
import type { ConnectionSummary } from "@/lib/api";
import { TableField } from "./table-picker";
import { ColumnsField } from "./columns-field";
import { SourceQueryField } from "./source-query-field";
import { PythonCodeEditor, PYTHON_CODE_STARTER } from "./python-code-editor";
import { CodeEditor } from "./code-editor";
import type { BuilderNode } from "@/lib/pipeline-config";
import {
  buildExpr,
  parseExpr,
  opNeedsValue,
  FILTER_OPS,
  type Condition,
  type FilterOp,
} from "@/lib/filter-expr";
import { cn } from "@/lib/cn";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

// Drawer width is user-resizable (2026-05-26 user request — "Transform 영역
// 너비 조절 가능하도록"). Sane defaults sit around the Monaco-comfortable mark,
// bounds keep the canvas usable.
const DRAWER_DEFAULT_WIDTH = 520;
const DRAWER_MIN_WIDTH = 380;
const DRAWER_MAX_WIDTH = 880;
const DRAWER_WIDTH_STORAGE_KEY = "etlx.builder.drawerWidth";

function _readStoredWidth(): number {
  if (typeof window === "undefined") return DRAWER_DEFAULT_WIDTH;
  try {
    const raw = window.localStorage.getItem(DRAWER_WIDTH_STORAGE_KEY);
    if (!raw) return DRAWER_DEFAULT_WIDTH;
    const n = Number(raw);
    if (!Number.isFinite(n)) return DRAWER_DEFAULT_WIDTH;
    return Math.max(DRAWER_MIN_WIDTH, Math.min(DRAWER_MAX_WIDTH, n));
  } catch {
    return DRAWER_DEFAULT_WIDTH;
  }
}

const FILTER_OP_LABEL: Record<FilterOp, keyof Messages> = {
  eq: "builder.opEq",
  ne: "builder.opNe",
  gt: "builder.opGt",
  gte: "builder.opGte",
  lt: "builder.opLt",
  lte: "builder.opLte",
  contains: "builder.opContains",
  empty: "builder.opEmpty",
  notEmpty: "builder.opNotEmpty",
};

/** Pull a table name out of a simple `... FROM <table> ...` SQL query so a
 *  downstream column picker can introspect it. Returns undefined for queries
 *  too complex to parse (joins/subqueries) — the picker then falls back to
 *  free-text entry. */
function parseTableFromQuery(query: unknown): string | undefined {
  if (typeof query !== "string") return undefined;
  const m = /\bfrom\s+([A-Za-z0-9_."`]+)/i.exec(query);
  if (!m) return undefined;
  return m[1].replace(/["`]/g, "");
}

/** Resolve the (connectionId, table) the column picker should introspect for
 *  the selected node: the node's own table for sinks, the upstream source's
 *  table for transforms. */
function resolveColumnsContext(
  op: OperatorSpec,
  node: BuilderNode,
  ownConnectionId: string | undefined,
  nodes: BuilderNode[],
  connections: ConnectionSummary[],
): { connectionId?: string; table?: string } {
  if (op.kind === "sink") {
    return { connectionId: ownConnectionId, table: node.data.table as string | undefined };
  }
  if (op.kind !== "transform") return {};
  const sourceNode = nodes.find((n) => findOperator(n.operatorId)?.kind === "source");
  const sourceOp = sourceNode && findOperator(sourceNode.operatorId);
  if (!sourceNode || !sourceOp) return {};
  const connField = sourceOp.fields.find((f) => f.kind === "connection");
  const connName = connField ? (sourceNode.data[connField.key] as string | undefined) : undefined;
  const connId = connName
    ? connections.find((c) => c.name === connName && c.type === sourceOp.connectorType)?.id
    : undefined;
  // MongoDB sources store the collection name directly in `query`; SQL sources
  // need the table parsed out of the SELECT.
  const table =
    sourceOp.connectorType === "mongodb"
      ? (sourceNode.data.query as string | undefined)
      : parseTableFromQuery(sourceNode.data.query);
  return { connectionId: connId, table };
}

export function PropertiesPanel({
  node,
  connections,
  workspaceId,
  nodes = [],
  pipelines = [],
  onChange,
  onClose,
  transformIndex = -1,
  transformCount = 0,
  onMove,
}: {
  node: BuilderNode | null;
  connections: ConnectionSummary[];
  /** Workspace id — needed for connection introspection (table pickers). */
  workspaceId?: string;
  /** All nodes in the linear pipeline — used to resolve the upstream source's
   *  table so downstream transforms can introspect its columns. */
  nodes?: BuilderNode[];
  /** Other pipelines in the workspace — for the call-pipeline target picker. */
  pipelines?: { id: string; name: string }[];
  onChange: (id: string, values: Record<string, unknown>) => void;
  /** Close handler — when set, a × button appears in the drawer header that
   *  deselects the node + collapses the drawer (graph-only mode, 2026-05-26). */
  onClose?: () => void;
  /** Index of this node within the transform run, or -1 if not a transform. */
  transformIndex?: number;
  transformCount?: number;
  onMove?: (id: string, dir: -1 | 1) => void;
}) {
  const { t } = useLocale();
  // Persisted drawer width (mouse-drag resizable, ADR-0041 — user request).
  const [drawerWidth, setDrawerWidth] = useState<number>(_readStoredWidth);
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(DRAWER_WIDTH_STORAGE_KEY, String(drawerWidth));
    } catch {
      /* private mode / quota — ignore */
    }
  }, [drawerWidth]);

  const startResize = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      const target = e.currentTarget;
      target.setPointerCapture(e.pointerId);
      const startX = e.clientX;
      const startWidth = drawerWidth;
      const onMove = (ev: PointerEvent) => {
        // Dragging the handle *left* widens the drawer (the handle lives on
        // the left edge of the right-side aside).
        const delta = startX - ev.clientX;
        const next = Math.max(
          DRAWER_MIN_WIDTH,
          Math.min(DRAWER_MAX_WIDTH, startWidth + delta),
        );
        setDrawerWidth(next);
      };
      const onUp = () => {
        target.removeEventListener("pointermove", onMove);
        target.removeEventListener("pointerup", onUp);
        target.removeEventListener("pointercancel", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };
      target.addEventListener("pointermove", onMove);
      target.addEventListener("pointerup", onUp);
      target.addEventListener("pointercancel", onUp);
      document.body.style.cursor = "col-resize";
      // Stop accidental text selection during the drag.
      document.body.style.userSelect = "none";
    },
    [drawerWidth],
  );

  if (!node) {
    return (
      <aside className="flex w-80 shrink-0 flex-col border-l border-border-subtle bg-surface px-4 py-6 text-sm text-text-muted">
        {t("builder.selectNode")}
      </aside>
    );
  }
  const op = findOperator(node.operatorId);
  if (!op) return null;

  const matchingConnections =
    op.kind === "source" || op.kind === "sink"
      ? connections.filter((c) => c.type === op.connectorType)
      : op.anyConnection
        ? connections
        : [];

  // Resolve the node's selected connection (stored by name) to its id so
  // table-picker fields can introspect it (ADR-0033).
  const connectionField = op.fields.find((f) => f.kind === "connection");
  const selectedConnName = connectionField
    ? (node.data[connectionField.key] as string | undefined)
    : undefined;
  const selectedConnectionId = selectedConnName
    ? matchingConnections.find((c) => c.name === selectedConnName)?.id
    : undefined;

  const columnsCtx = resolveColumnsContext(
    op,
    node,
    selectedConnectionId,
    nodes,
    connections,
  );

  return (
    // Drawer — width is mouse-drag resizable via the left-edge handle
    // (default 520, user request 2026-05-26). The Monaco editor, columns
    // picker, mapping table, and JSON fields all benefit from extra
    // horizontal room. Persisted in localStorage so the preference survives
    // navigation + reload. Click another node and the content swaps in
    // place at the user's chosen width. Settings (retry/dlq/variables/
    // triggers) sit behind and reappear when the node is deselected.
    <aside
      key={node.id}
      className="relative flex shrink-0 flex-col gap-4 overflow-y-auto border-l border-border-subtle bg-surface px-4 py-5"
      style={{ width: drawerWidth }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label={t("builder.resizeDrawer")}
        title={t("builder.resizeDrawer")}
        onPointerDown={startResize}
        className="absolute inset-y-0 left-0 z-10 w-1.5 cursor-col-resize bg-transparent transition-colors duration-150 hover:bg-accent/40"
      />
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div
            className="text-[11px] font-semibold uppercase tracking-widest text-text-muted underline decoration-dotted decoration-text-muted/40 underline-offset-2"
            title={
              op.kind === "source"
                ? t("glossary.source")
                : op.kind === "sink"
                  ? t("glossary.sink")
                  : op.kind === "transform"
                    ? t("glossary.transform")
                    : ""
            }
          >
            {op.kind}
          </div>
          <div className="mt-1 text-base font-semibold text-text">{getOperatorLabel(op, t)}</div>
          <p className="mt-1 text-xs text-text-secondary">{getOperatorDescription(op, t)}</p>
        </div>
        {onClose ? (
          <button
            type="button"
            onClick={onClose}
            aria-label={t("common.close")}
            title={t("common.close")}
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-text-muted transition duration-150 hover:bg-overlay hover:text-text"
          >
            <XCircleIcon size={16} />
          </button>
        ) : null}
      </header>

      {/* Incomplete badge: the audit found that "Not configured" wasn't
          enough — analysts didn't know which field was missing or what
          to do about it. Compute the first empty required field, name
          it explicitly, and tell the user where to look (the form
          field below has a red border to match). */}
      {(() => {
        // Phase AAF (2026-05-29): mirror pipeline-node.tsx — only
        // consider visible required fields. A hidden field can't be
        // the "next step" the user has to take.
        const firstMissing = op.fields.find(
          (f) =>
            f.required &&
            (!f.showWhen || node.data[f.showWhen.field] === f.showWhen.equals) &&
            (node.data[f.key] === undefined ||
              node.data[f.key] === null ||
              node.data[f.key] === ""),
        );
        if (!firstMissing) return null;
        return (
          <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-xs text-warning">
            <div className="flex items-center gap-1.5 font-semibold">
              <span aria-hidden>⚠</span>
              {t("builder.nodeIncomplete")}
            </div>
            <div className="mt-1 text-warning/90">
              {t("builder.nextStep", { field: firstMissing.label })}
            </div>
          </div>
        );
      })()}

      <div className="flex flex-col gap-4">
        {op.fields
          .filter((field) => {
            // Phase AAF (2026-05-29): conditional visibility. The
            // ``showWhen`` predicate keeps dependent fields out of the
            // panel until their controlling field is set — e.g.
            // ``auto_create_if_exists`` only renders once
            // ``auto_create_table`` is on, so off-by-default toggles
            // don't drag their settings along as clutter.
            if (!field.showWhen) return true;
            return node.data[field.showWhen.field] === field.showWhen.equals;
          })
          .map((field) => (
            <FieldEditor
              key={`${node.id}:${field.key}`}
              field={field}
              value={node.data[field.key]}
              connections={matchingConnections}
              pipelines={pipelines}
              workspaceId={workspaceId}
              connectionId={selectedConnectionId}
              columnsConnectionId={columnsCtx.connectionId}
              columnsTable={columnsCtx.table}
              t={t}
              onChange={(v) =>
                onChange(node.id, { ...node.data, [field.key]: v })
              }
            />
          ))}
      </div>
    </aside>
  );
}

function FieldEditor({
  field,
  value,
  connections,
  pipelines,
  workspaceId,
  connectionId,
  columnsConnectionId,
  columnsTable,
  onChange,
  t,
}: {
  field: FieldDef;
  value: unknown;
  connections: ConnectionSummary[];
  pipelines: { id: string; name: string }[];
  workspaceId?: string;
  connectionId?: string;
  columnsConnectionId?: string;
  columnsTable?: string;
  onChange: (v: unknown) => void;
  t: Translate;
}) {
  const isEmpty = value === undefined || value === null || value === "";
  const showRequired = Boolean(field.required) && isEmpty;
  // Fields with more than one interactive control (checklists, condition/
  // mapping builders, the SQL field's "browse tables" button) must NOT be
  // wrapped in a <label> — a label forwards clicks from its whitespace to its
  // first control, so clicking empty space would toggle a checkbox / focus an
  // input. Single-control fields keep the <label> for click-to-focus.
  //
  // ``pythonCode`` (Monaco) is composite too: Monaco renders a hidden textarea
  // plus a scroll widget, suggestion popup, and minimap (when on) — wrapping
  // any of that in a <label> hijacks pointer events back to the textarea on
  // every click, kicking the cursor out of the editor mid-edit (user report
  // 2026-05-26 "커서가 자꾸 밖으로 튕겨"). Treat as composite to keep clicks
  // inside Monaco.
  const composite =
    field.kind === "filter" ||
    field.kind === "mapping" ||
    field.kind === "columns" ||
    field.kind === "sourceQuery" ||
    field.kind === "table" ||
    field.kind === "pythonCode" ||
    field.kind === "sql" ||
    field.kind === "json";
  const Wrapper = composite ? "div" : "label";
  // When a required field is empty, paint the closest interactive
  // descendant (select / input / textarea) with a red border via a
  // descendant selector. Cheaper + safer than threading a prop into
  // every branch of FieldInput. The ring stays even on focus so the
  // visual feedback survives until the user fills the value in.
  const requiredEmptyOutline = showRequired
    ? "[&_select]:border-error [&_input:not([type='checkbox'])]:border-error [&_textarea]:border-error"
    : "";
  return (
    <Wrapper className={`flex flex-col gap-1.5 ${requiredEmptyOutline}`}>
      <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-text-secondary">
        {field.label}
        {field.required ? (
          <span
            className={
              showRequired
                ? "inline-flex h-4 items-center rounded-sm bg-warning/15 px-1 text-[10px] font-semibold uppercase text-warning"
                : "inline-flex h-4 items-center rounded-sm bg-overlay px-1 text-[10px] font-semibold uppercase text-text-muted"
            }
            title={t("builder.required")}
          >
            {t("builder.required")}
          </span>
        ) : null}
      </span>
      <FieldInput
        field={field}
        value={value}
        connections={connections}
        pipelines={pipelines}
        workspaceId={workspaceId}
        connectionId={connectionId}
        columnsConnectionId={columnsConnectionId}
        columnsTable={columnsTable}
        onChange={onChange}
        t={t}
      />
      {showRequired ? (
        <span className="text-[11px] text-warning">{t("builder.fieldRequired")}</span>
      ) : field.help ? (
        <span className="text-[11px] text-text-muted">{field.help}</span>
      ) : null}
    </Wrapper>
  );
}

function FieldInput({
  field,
  value,
  connections,
  pipelines,
  workspaceId,
  connectionId,
  columnsConnectionId,
  columnsTable,
  onChange,
  t,
}: {
  field: FieldDef;
  value: unknown;
  connections: ConnectionSummary[];
  pipelines: { id: string; name: string }[];
  workspaceId?: string;
  connectionId?: string;
  columnsConnectionId?: string;
  columnsTable?: string;
  onChange: (v: unknown) => void;
  t: Translate;
}) {
  if (field.kind === "pipeline") {
    return (
      <select
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value || undefined)}
        className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
      >
        <option value="">{t("builder.selectPipeline")}</option>
        {pipelines.length === 0 ? (
          <option disabled value="">
            {t("builder.callPipelineEmpty")}
          </option>
        ) : null}
        {pipelines.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    );
  }
  if (field.kind === "connection") {
    // Phase ADU (2026-06-04) — the value is a connection *name*. If it
    // names a connection that no longer exists (deleted/renamed) the
    // select silently falls back to the placeholder, so a stale-but-
    // informative reference looks merely "unset" and the next run would
    // fail to build. Flag it so the operator re-picks deliberately.
    const connValue = (value as string) ?? "";
    const missingRef =
      connValue.length > 0 && !connections.some((c) => c.name === connValue);
    return (
      <>
        <select
          value={connValue}
          onChange={(e) => onChange(e.target.value || undefined)}
          className={`h-10 rounded-md border bg-elevated px-2 text-sm text-text focus-visible:outline-none ${
            missingRef
              ? "border-error focus-visible:border-error"
              : "border-border-subtle focus-visible:border-accent"
          }`}
        >
          <option value="">{t("builder.selectConnection")}</option>
          {/* Keep the stale name selectable so the field shows what it
              pointed at until the operator changes it. */}
          {missingRef ? (
            <option value={connValue}>{connValue}</option>
          ) : null}
          {connections.length === 0 ? (
            <option disabled value="">
              {t("builder.noConnectionsOfType")}
            </option>
          ) : null}
          {connections.map((c) => (
            <option key={c.id} value={c.name}>
              {c.name}
            </option>
          ))}
        </select>
        {missingRef ? (
          <span className="mt-1 text-xs text-error">
            {t("builder.missingConnectionRef", { name: connValue })}
          </span>
        ) : null}
      </>
    );
  }
  if (field.kind === "table") {
    return (
      <TableField
        value={value}
        placeholder={field.placeholder}
        workspaceId={workspaceId}
        connectionId={connectionId}
        onChange={onChange}
        t={t}
      />
    );
  }
  if (field.kind === "sourceQuery") {
    return (
      <SourceQueryField
        value={value}
        placeholder={field.placeholder}
        workspaceId={workspaceId}
        connectionId={connectionId}
        onChange={onChange}
        t={t}
      />
    );
  }
  if (field.kind === "select") {
    return (
      <select
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value || undefined)}
        className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
      >
        <option value="">—</option>
        {field.options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  if (field.kind === "boolean") {
    // Phase YY (ADR-0069, 2026-05-29): boolean toggle. Stores ``true`` /
    // ``false`` in the node's data; the serialiser drops the key when
    // ``false`` to keep configs minimal.
    const checked =
      typeof value === "boolean" ? value : Boolean(field.defaultValue);
    return (
      <label className="flex cursor-pointer items-center gap-2 text-sm text-text">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked || undefined)}
          className="h-4 w-4 cursor-pointer accent-accent"
        />
        <span className="select-none">{field.label}</span>
      </label>
    );
  }
  if (field.kind === "number") {
    return (
      <Input
        type="number"
        value={value == null ? "" : String(value)}
        placeholder={field.placeholder}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "") onChange(undefined);
          else onChange(Number(v));
        }}
      />
    );
  }
  if (field.kind === "columns") {
    return (
      <ColumnsField
        value={value}
        workspaceId={workspaceId}
        connectionId={columnsConnectionId}
        table={columnsTable}
        onChange={onChange}
        t={t}
      />
    );
  }
  if (field.kind === "json") {
    return <JsonInput value={value} onChange={onChange} field={field} t={t} />;
  }
  if (field.kind === "mapping") {
    return (
      <MappingEditor
        value={value}
        onChange={onChange}
        mappingKind={field.mappingKind}
        t={t}
      />
    );
  }
  if (field.kind === "filter") {
    return <FilterEditor value={value} onChange={onChange} t={t} />;
  }
  if (field.kind === "pythonCode") {
    // Seed the starter ``transform(record)`` skeleton **only when the
    // field is truly unset** (never edited). If the user explicitly
    // cleared the editor (stored ""), respect that — re-injecting the
    // starter on every reload would silently overwrite the user's
    // intent. The save-side validation surfaces blank code as a clear
    // error toast instead of papering over it.
    const stored = value as string | undefined;
    const initial = stored ?? PYTHON_CODE_STARTER;
    // FieldEditor (the wrapper) already keys by ``${node.id}:${field.key}``,
    // so switching to a different node remounts the editor with a fresh
    // defaultValue. Within the same selected node Monaco stays mounted —
    // that's the whole point of the uncontrolled rewrite (cursor stable).
    return (
      <PythonCodeEditor
        value={initial}
        // Pass empty strings through (don't coerce to undefined) so a
        // deliberate clear sticks across reloads.
        onChange={(next) => onChange(next)}
      />
    );
  }
  if (field.kind === "sql") {
    // Phase ADX (2026-06-04) — SQL statement (sql_exec "Run SQL"). Same
    // Monaco IDE as pythonCode, SQL grammar. No starter skeleton — a SQL
    // statement has no boilerplate shape. 240px ≈ ~12 lines, enough for a
    // DELETE/MERGE without dominating the panel. Uncontrolled (the
    // wrapper keys per node:field, so node switches remount it).
    return (
      <div className="flex flex-col gap-1">
        <CodeEditor
          language="sql"
          value={(value as string | undefined) ?? ""}
          onChange={(next) => onChange(next)}
          height={240}
          tabSize={2}
        />
        {/* Phase ADY — Monaco hides the placeholder; show the example. */}
        {field.placeholder ? (
          <span className="text-[11px] text-text-muted">
            {t("builder.sqlExample", { example: field.placeholder })}
          </span>
        ) : null}
      </div>
    );
  }
  if (field.kind === "string" && field.multiline) {
    return (
      <textarea
        rows={4}
        value={(value as string) ?? ""}
        placeholder={field.placeholder}
        onChange={(e) => onChange(e.target.value || undefined)}
        className={cn(
          "min-h-20 w-full rounded-md border border-border-subtle bg-elevated px-3 py-2 font-mono text-xs text-text",
          "transition duration-200 focus-visible:border-accent focus-visible:outline-none",
        )}
      />
    );
  }
  return (
    <Input
      value={(value as string) ?? ""}
      placeholder={field.placeholder}
      onChange={(e) => onChange(e.target.value || undefined)}
    />
  );
}

let _condSeq = 0;
function nextCondId(): string {
  _condSeq += 1;
  return `cond-${_condSeq}`;
}

/**
 * No-code filter builder: AND-joined (field / operator / value) rows that
 * generate the Python expression the core filter expects. An "advanced"
 * toggle reveals the raw expression for OR / functions / anything the simple
 * builder can't express; if the raw text parses back to simple conditions
 * the user can switch back. Remounted per node (keyed by node id) so it
 * re-seeds from the stored value.
 */
export function FilterEditor({
  value,
  onChange,
  t,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
  t: Translate;
}) {
  const initialExpr = typeof value === "string" ? value : "";
  const initialParsed = parseExpr(initialExpr);
  type Row = Condition & { id: string };
  const [advanced, setAdvanced] = useState(initialParsed === null);
  const [raw, setRaw] = useState(initialExpr);
  const [rows, setRows] = useState<Row[]>(
    (initialParsed ?? []).map((c) => ({ ...c, id: nextCondId() })),
  );

  function commitRows(next: Row[]) {
    setRows(next);
    const expr = buildExpr(next);
    setRaw(expr);
    onChange(expr || undefined);
  }

  function commitRaw(text: string) {
    setRaw(text);
    onChange(text.trim() || undefined);
  }

  if (advanced) {
    const canSimplify = parseExpr(raw) !== null;
    return (
      <div className="flex flex-col gap-2">
        <textarea
          rows={4}
          value={raw}
          placeholder="data['amount'] > 0 and data['type'] in ('a','b')"
          onChange={(e) => commitRaw(e.target.value)}
          className={cn(
            "min-h-20 w-full rounded-md border border-border-subtle bg-elevated px-3 py-2 font-mono text-xs text-text",
            "transition duration-200 focus-visible:border-accent focus-visible:outline-none",
          )}
        />
        <span className="text-[11px] text-text-muted">
          {t("builder.filterAdvancedNote")}
        </span>
        <button
          type="button"
          disabled={!canSimplify}
          onClick={() => {
            const parsed = parseExpr(raw) ?? [];
            setRows(parsed.map((c) => ({ ...c, id: nextCondId() })));
            setAdvanced(false);
          }}
          className="self-start rounded-sm border border-border-subtle px-2 py-1 text-xs text-text-secondary transition duration-150 hover:border-border-strong hover:bg-overlay hover:text-text disabled:opacity-40"
        >
          {t("builder.toSimple")}
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {rows.length === 0 ? (
        <p className="text-[11px] text-text-muted">{t("builder.filterEmpty")}</p>
      ) : (
        <>
          <p className="text-[11px] text-text-muted">{t("builder.filterAll")}</p>
          <div className="flex flex-col gap-1.5">
            {rows.map((row, i) => (
              <div key={row.id} className="flex items-center gap-1.5">
                <Input
                  value={row.field}
                  placeholder={t("builder.filterField")}
                  className="min-w-0 flex-1"
                  onChange={(e) =>
                    commitRows(
                      rows.map((r, j) =>
                        j === i ? { ...r, field: e.target.value } : r,
                      ),
                    )
                  }
                />
                <select
                  value={row.op}
                  onChange={(e) =>
                    commitRows(
                      rows.map((r, j) =>
                        j === i ? { ...r, op: e.target.value as FilterOp } : r,
                      ),
                    )
                  }
                  className="h-10 shrink-0 rounded-md border border-border-subtle bg-elevated px-1.5 text-xs text-text focus-visible:border-accent focus-visible:outline-none"
                >
                  {FILTER_OPS.map((op) => (
                    <option key={op} value={op}>
                      {t(FILTER_OP_LABEL[op])}
                    </option>
                  ))}
                </select>
                {opNeedsValue(row.op) ? (
                  <Input
                    value={row.value}
                    placeholder={t("builder.filterValue")}
                    className="min-w-0 flex-1"
                    onChange={(e) =>
                      commitRows(
                        rows.map((r, j) =>
                          j === i ? { ...r, value: e.target.value } : r,
                        ),
                      )
                    }
                  />
                ) : (
                  <span className="flex-1" />
                )}
                <button
                  type="button"
                  aria-label={t("builder.removeRow")}
                  onClick={() => commitRows(rows.filter((_, j) => j !== i))}
                  className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-text-muted transition duration-150 hover:bg-overlay hover:text-error"
                >
                  <XIcon size={14} />
                </button>
              </div>
            ))}
          </div>
        </>
      )}
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() =>
            commitRows([
              ...rows,
              { id: nextCondId(), field: "", op: "eq", value: "" },
            ])
          }
          className="inline-flex items-center gap-1.5 rounded-sm border border-border-subtle px-2 py-1 text-xs text-text-secondary transition duration-150 hover:border-border-strong hover:bg-overlay hover:text-text"
        >
          <PlusIcon size={13} />
          {t("builder.addCondition")}
        </button>
        <button
          type="button"
          onClick={() => {
            setRaw(buildExpr(rows));
            setAdvanced(true);
          }}
          className="text-[11px] text-text-muted transition duration-150 hover:text-text"
        >
          {t("builder.toAdvanced")}
        </button>
      </div>
    </div>
  );
}

const CAST_TYPES = ["int", "float", "str", "bool", "timestamp"];

let _rowSeq = 0;
function nextRowId(): string {
  _rowSeq += 1;
  return `row-${_rowSeq}`;
}

/**
 * No-code key→value table for the rename/cast transforms. Builds a flat
 * `{ column: value }` object — identical wire shape to the old raw-JSON
 * field — so non-developers never touch JSON. Local row state owns the
 * in-progress edit (incl. blank keys); the parent remounts this per node
 * (keyed by node id) so switching nodes re-seeds from the stored value.
 */
function MappingEditor({
  value,
  onChange,
  mappingKind,
  t,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
  mappingKind: "rename" | "cast";
  t: Translate;
}) {
  type Row = { id: string; k: string; v: string };
  const [rows, setRows] = useState<Row[]>(() => {
    const obj = (value as Record<string, unknown>) ?? {};
    return Object.entries(obj).map(([k, v]) => ({
      id: nextRowId(),
      k,
      v: String(v),
    }));
  });

  function commit(next: Row[]) {
    setRows(next);
    const obj: Record<string, string> = {};
    for (const r of next) {
      const key = r.k.trim();
      if (key && r.v !== "") obj[key] = r.v;
    }
    onChange(Object.keys(obj).length ? obj : undefined);
  }

  const defaultValue = mappingKind === "cast" ? "str" : "";

  return (
    <div className="flex flex-col gap-2">
      {rows.length === 0 ? (
        <p className="text-[11px] text-text-muted">{t("builder.mapEmpty")}</p>
      ) : (
        <div className="flex flex-col gap-1.5">
          <div className="flex gap-2 text-[10px] font-semibold uppercase tracking-wider text-text-muted">
            <span className="flex-1">
              {mappingKind === "cast" ? t("builder.castColumn") : t("builder.mapFrom")}
            </span>
            <span className="flex-1">
              {mappingKind === "cast" ? t("builder.castType") : t("builder.mapTo")}
            </span>
            <span className="w-7" />
          </div>
          {rows.map((row, i) => (
            <div key={row.id} className="flex items-center gap-2">
              <Input
                value={row.k}
                placeholder="column"
                className="flex-1"
                onChange={(e) =>
                  commit(rows.map((r, j) => (j === i ? { ...r, k: e.target.value } : r)))
                }
              />
              {mappingKind === "cast" ? (
                <select
                  value={row.v || "str"}
                  onChange={(e) =>
                    commit(rows.map((r, j) => (j === i ? { ...r, v: e.target.value } : r)))
                  }
                  className="h-10 flex-1 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
                >
                  {CAST_TYPES.map((ty) => (
                    <option key={ty} value={ty}>
                      {ty}
                    </option>
                  ))}
                </select>
              ) : (
                <Input
                  value={row.v}
                  placeholder="new_name"
                  className="flex-1"
                  onChange={(e) =>
                    commit(rows.map((r, j) => (j === i ? { ...r, v: e.target.value } : r)))
                  }
                />
              )}
              <button
                type="button"
                aria-label={t("builder.removeRow")}
                onClick={() => commit(rows.filter((_, j) => j !== i))}
                className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-text-muted transition duration-150 hover:bg-overlay hover:text-error"
              >
                <XIcon size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
      <button
        type="button"
        onClick={() =>
          commit([...rows, { id: nextRowId(), k: "", v: defaultValue }])
        }
        className="inline-flex items-center gap-1.5 self-start rounded-sm border border-border-subtle px-2 py-1 text-xs text-text-secondary transition duration-150 hover:border-border-strong hover:bg-overlay hover:text-text"
      >
        <PlusIcon size={13} />
        {t("builder.addRow")}
      </button>
    </div>
  );
}

function JsonInput({
  value,
  onChange,
  field,
  t,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
  field: Extract<FieldDef, { kind: "json" }>;
  t: Translate;
}) {
  // Phase AEA (2026-06-04) — Monaco JSON editor. Monaco ships a JSON
  // language service so invalid JSON gets real red squiggles inline (the
  // old textarea only surfaced a parse error after the fact). Uncontrolled
  // like the Python/SQL editors: ``value`` is the initial buffer; the
  // FieldEditor wrapper keys per node:field so node switches remount it.
  const initial = value === undefined ? "" : JSON.stringify(value, null, 2);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="flex flex-col gap-1">
      <CodeEditor
        language="json"
        value={initial}
        height={160}
        tabSize={2}
        onChange={(txt) => {
          if (txt.trim() === "") {
            setError(null);
            onChange(undefined);
            return;
          }
          try {
            onChange(JSON.parse(txt));
            setError(null);
          } catch (err) {
            // Keep the last valid value in the parent (don't push a broken
            // parse); Monaco's squiggles + this line tell the user.
            setError(err instanceof Error ? err.message : String(err));
          }
        }}
      />
      {field.placeholder ? (
        <span className="text-[11px] text-text-muted">
          {t("builder.sqlExample", { example: field.placeholder })}
        </span>
      ) : null}
      {error ? (
        <span className="text-[11px] text-error">
          {t("builder.jsonError", { error })}
        </span>
      ) : null}
    </div>
  );
}
