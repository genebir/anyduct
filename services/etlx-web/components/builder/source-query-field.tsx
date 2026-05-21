"use client";

import { useState } from "react";
import { cn } from "@/lib/cn";
import { useTables, groupBySchema } from "./table-picker";
import { useColumns } from "./columns-field";
import type { Messages } from "@/lib/i18n/messages";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

interface Parsed {
  schema: string;
  table: string;
  columns: string[];
}

/** Parse a simple `SELECT <cols> FROM <table>` query so the visual builder can
 *  pre-fill. Returns null for anything more complex (WHERE / JOIN / subquery)
 *  — those stay in raw-SQL mode. */
export function parseSourceQuery(query: unknown): Parsed | null {
  if (typeof query !== "string") return null;
  const m = /^\s*select\s+(.+?)\s+from\s+([A-Za-z0-9_."`]+)\s*;?\s*$/i.exec(query);
  if (!m) return null;
  const colPart = m[1].trim();
  const ref = m[2].replace(/["`]/g, "");
  const dot = ref.indexOf(".");
  const schema = dot >= 0 ? ref.slice(0, dot) : "";
  const table = dot >= 0 ? ref.slice(dot + 1) : ref;
  const columns =
    colPart === "*"
      ? []
      : colPart
          .split(",")
          .map((c) => c.trim().replace(/["`]/g, ""))
          .filter(Boolean);
  return { schema, table, columns };
}

function buildQuery(schema: string, table: string, columns: string[]): string {
  if (!table) return "";
  const ref = schema ? `${schema}.${table}` : table;
  const cols = columns.length ? columns.join(", ") : "*";
  return `SELECT ${cols} FROM ${ref}`;
}

export function SourceQueryField({
  value,
  placeholder,
  workspaceId,
  connectionId,
  onChange,
  t,
}: {
  value: unknown;
  placeholder?: string;
  workspaceId?: string;
  connectionId?: string;
  onChange: (v: unknown) => void;
  t: Translate;
}) {
  const parsed = parseSourceQuery(value);
  const hasText = typeof value === "string" && value.trim().length > 0;
  const [mode, setMode] = useState<"sql" | "visual">(() =>
    hasText && !parsed ? "sql" : "visual",
  );
  const [schema, setSchema] = useState(parsed?.schema ?? "");
  const [table, setTable] = useState(parsed?.table ?? "");
  const [columns, setColumns] = useState<string[]>(parsed?.columns ?? []);

  return (
    <div className="flex flex-col gap-2">
      <div
        role="tablist"
        className="inline-flex w-fit rounded-md border border-border-subtle bg-elevated p-0.5"
      >
        <ModeTab active={mode === "visual"} onClick={() => setMode("visual")}>
          {t("builder.sourceModeVisual")}
        </ModeTab>
        <ModeTab
          active={mode === "sql"}
          onClick={() => {
            setMode("sql");
          }}
        >
          {t("builder.sourceModeSql")}
        </ModeTab>
      </div>

      {mode === "sql" ? (
        <textarea
          rows={4}
          value={(value as string) ?? ""}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value || undefined)}
          className={cn(
            "min-h-20 w-full rounded-md border border-border-subtle bg-elevated px-3 py-2 font-mono text-xs text-text",
            "transition duration-200 focus-visible:border-accent focus-visible:outline-none",
          )}
        />
      ) : (
        <VisualBuilder
          workspaceId={workspaceId}
          connectionId={connectionId}
          schema={schema}
          table={table}
          columns={columns}
          onSchema={(s) => {
            setSchema(s);
            setTable("");
            setColumns([]);
            onChange(undefined);
          }}
          onTable={(tbl) => {
            setTable(tbl);
            setColumns([]);
            onChange(buildQuery(schema, tbl, []) || undefined);
          }}
          onColumns={(cols) => {
            setColumns(cols);
            onChange(buildQuery(schema, table, cols) || undefined);
          }}
          t={t}
        />
      )}
    </div>
  );
}

function ModeTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "rounded-sm px-2.5 py-1 text-xs font-medium transition duration-150",
        active
          ? "bg-surface text-text shadow-sm"
          : "text-text-muted hover:text-text-secondary",
      )}
    >
      {children}
    </button>
  );
}

function VisualBuilder({
  workspaceId,
  connectionId,
  schema,
  table,
  columns,
  onSchema,
  onTable,
  onColumns,
  t,
}: {
  workspaceId?: string;
  connectionId?: string;
  schema: string;
  table: string;
  columns: string[];
  onSchema: (s: string) => void;
  onTable: (t: string) => void;
  onColumns: (c: string[]) => void;
  t: Translate;
}) {
  const { tables, loading, error } = useTables(workspaceId, connectionId, true);
  const bySchema = groupBySchema(tables);
  const schemas = [...bySchema.keys()].sort();
  const showSchema = schemas.length > 1 || (schemas.length === 1 && schemas[0] !== "");
  const effectiveSchema = showSchema ? schema : (schemas[0] ?? "");
  const tablesForSchema = bySchema.get(effectiveSchema) ?? [];
  const fullId = table ? (effectiveSchema ? `${effectiveSchema}.${table}` : table) : undefined;

  const cols = useColumns(workspaceId, connectionId, fullId, Boolean(fullId));

  if (!connectionId) {
    return <p className="text-[11px] text-text-muted">{t("builder.tableSelectConnFirst")}</p>;
  }
  if (loading) {
    return <p className="text-[11px] text-text-muted">{t("builder.tableLoading")}</p>;
  }
  if (error) {
    return <p className="text-[11px] text-warning">{t("builder.tableLoadError")}</p>;
  }

  return (
    <div className="flex flex-col gap-2.5">
      {showSchema ? (
        <Labeled label={t("builder.sourceSchema")}>
          <Select value={schema} onChange={onSchema} placeholder={t("builder.sourceSchemaPick")}>
            {schemas.map((s) => (
              <option key={s} value={s}>
                {s || t("builder.sourceSchemaDefault")}
              </option>
            ))}
          </Select>
        </Labeled>
      ) : null}

      <Labeled label={t("builder.sourceTable")}>
        <Select value={table} onChange={onTable} placeholder={t("builder.sourceTablePick")}>
          {tablesForSchema.map((tbl) => (
            <option key={tbl.fullId} value={tbl.name}>
              {tbl.name}
            </option>
          ))}
        </Select>
      </Labeled>

      {table ? (
        <Labeled label={t("builder.sourceColumns")}>
          {cols.loading ? (
            <span className="text-[11px] text-text-muted">{t("builder.columnsLoading")}</span>
          ) : cols.error ? (
            <span className="text-[11px] text-warning">{t("builder.columnsLoadError")}</span>
          ) : cols.columns.length === 0 ? (
            <span className="text-[11px] text-text-muted">{t("builder.columnsNone")}</span>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-text-muted">
                  {columns.length === 0
                    ? t("builder.sourceAllColumns")
                    : t("builder.sourceNColumns", { n: columns.length })}
                </span>
                <button
                  type="button"
                  onClick={() => onColumns([])}
                  disabled={columns.length === 0}
                  className="text-[11px] text-text-muted transition duration-150 hover:text-text disabled:opacity-40"
                >
                  {t("builder.sourceColumnsAll")}
                </button>
              </div>
              <div className="flex max-h-48 flex-col gap-0.5 overflow-y-auto rounded-md border border-border-subtle bg-elevated p-1.5">
                {cols.columns.map((col) => {
                  const checked = columns.includes(col.name);
                  return (
                    <label
                      key={col.name}
                      className="flex cursor-pointer items-center gap-2 rounded-sm px-1.5 py-1 text-xs text-text-secondary transition duration-150 hover:bg-overlay"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() =>
                          onColumns(
                            checked
                              ? columns.filter((c) => c !== col.name)
                              : [...columns, col.name],
                          )
                        }
                        className="accent-[rgb(var(--accent))]"
                      />
                      <span className="font-mono">{col.name}</span>
                      {col.type ? (
                        <span className="ml-auto font-mono text-[10px] text-text-muted">
                          {col.type}
                        </span>
                      ) : null}
                    </label>
                  );
                })}
              </div>
            </>
          )}
        </Labeled>
      ) : null}
    </div>
  );
}

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-text-secondary">
        {label}
      </span>
      {children}
    </div>
  );
}

function Select({
  value,
  onChange,
  placeholder,
  children,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  children: React.ReactNode;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
    >
      <option value="">{placeholder}</option>
      {children}
    </select>
  );
}
