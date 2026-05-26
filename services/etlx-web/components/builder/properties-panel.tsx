"use client";

import { useEffect, useState } from "react";
import { PlusIcon, XCircleIcon, XIcon } from "lucide-react";
import { Input } from "@/components/ui/input";
import { findOperator, type FieldDef, type OperatorSpec } from "@/lib/operators";
import type { ConnectionSummary } from "@/lib/api";
import { TableField } from "./table-picker";
import { ColumnsField } from "./columns-field";
import { SourceQueryField } from "./source-query-field";
import { PythonCodeEditor, PYTHON_CODE_STARTER } from "./python-code-editor";
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
    // Drawer — wider than the prior 320px panel (2026-05-26) so the Monaco
    // editor, columns picker, mapping table, and JSON fields have room to
    // breathe. Click another node and the content swaps in place. Settings
    // (retry/dlq/variables/triggers) sit behind it and become visible again
    // when the node is deselected (× button or click empty canvas).
    <aside
      key={node.id}
      className="flex w-[520px] shrink-0 flex-col gap-4 overflow-y-auto border-l border-border-subtle bg-surface px-4 py-5"
    >
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-semibold uppercase tracking-widest text-text-muted">
            {op.kind}
          </div>
          <div className="mt-1 text-base font-semibold text-text">{op.label}</div>
          <p className="mt-1 text-xs text-text-secondary">{op.description}</p>
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

      <div className="flex flex-col gap-4">
        {op.fields.map((field) => (
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
  const composite =
    field.kind === "filter" ||
    field.kind === "mapping" ||
    field.kind === "columns" ||
    field.kind === "sourceQuery" ||
    field.kind === "table";
  const Wrapper = composite ? "div" : "label";
  return (
    <Wrapper className="flex flex-col gap-1.5">
      <span className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-text-secondary">
        {field.label}
        {field.required ? (
          <span className="text-accent" title={t("builder.required")} aria-hidden>
            *
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
    return (
      <select
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value || undefined)}
        className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
      >
        <option value="">{t("builder.selectConnection")}</option>
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
    // Seed a starter ``transform(record)`` skeleton so first-time users have
    // something runnable in the editor instead of a blank canvas.
    const initial = (value as string) ?? "";
    return (
      <PythonCodeEditor
        value={initial || PYTHON_CODE_STARTER}
        onChange={(next) => onChange(next || undefined)}
      />
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
  const [text, setText] = useState<string>(() =>
    value === undefined ? "" : JSON.stringify(value, null, 2),
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setText(value === undefined ? "" : JSON.stringify(value, null, 2));
  }, [value]);

  return (
    <div className="flex flex-col gap-1">
      <textarea
        rows={4}
        value={text}
        placeholder={field.placeholder}
        onChange={(e) => {
          const txt = e.target.value;
          setText(txt);
          if (txt.trim() === "") {
            setError(null);
            onChange(undefined);
            return;
          }
          try {
            onChange(JSON.parse(txt));
            setError(null);
          } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
          }
        }}
        className={cn(
          "min-h-20 w-full rounded-md border border-border-subtle bg-elevated px-3 py-2 font-mono text-xs text-text",
          "transition duration-200 focus-visible:border-accent focus-visible:outline-none",
          error && "border-error",
        )}
      />
      {error ? (
        <span className="text-[11px] text-error">
          {t("builder.jsonError", { error })}
        </span>
      ) : null}
    </div>
  );
}
