"use client";

import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { connectionsApi } from "@/lib/api";
import type { Messages } from "@/lib/i18n/messages";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

/**
 * Lazily introspect a connection's tables (ADR-0033). Fetches once `enabled`
 * flips true (e.g. on focus / open) and a connection is selected; re-fetches
 * when the connection changes. Returns a stable status the pickers render.
 */
export function useTables(
  workspaceId: string | undefined,
  connectionId: string | undefined,
  enabled: boolean,
): { tables: string[]; loading: boolean; error: string | null } {
  const [tables, setTables] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !workspaceId || !connectionId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    connectionsApi
      .tables(workspaceId, connectionId)
      .then((res) => {
        if (!cancelled) setTables(res.tables);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, connectionId, enabled]);

  return { tables, loading, error };
}

const TABLE_DATALIST_PREFIX = "etlx-tables-";
let _datalistSeq = 0;
const SELECT_CLS =
  "h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none";

/** Group introspected "schema.table" / "table" names by schema. Bare names
 *  (MySQL/SQLite) land under the "" (default) schema. */
export function groupBySchema(
  tables: string[],
): Map<string, { name: string; fullId: string }[]> {
  const bySchema = new Map<string, { name: string; fullId: string }[]>();
  for (const full of tables) {
    const dot = full.indexOf(".");
    const schema = dot >= 0 ? full.slice(0, dot) : "";
    const name = dot >= 0 ? full.slice(dot + 1) : full;
    const arr = bySchema.get(schema) ?? [];
    arr.push({ name, fullId: full });
    bySchema.set(schema, arr);
  }
  return bySchema;
}

function parseTableRef(value: unknown): { schema: string; table: string } {
  if (typeof value !== "string" || !value) return { schema: "", table: "" };
  const dot = value.indexOf(".");
  return dot >= 0
    ? { schema: value.slice(0, dot), table: value.slice(dot + 1) }
    : { schema: "", table: value };
}

/** Table/collection picker: schema → table dropdowns from introspection
 *  (ADR-0033), plus a free-text input so sinks can target tables that don't
 *  exist yet (or that introspection can't reach). The dropdowns and the input
 *  drive the same stored "schema.table" string. */
export function TableField({
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
  const [listId] = useState(() => `${TABLE_DATALIST_PREFIX}${(_datalistSeq += 1)}`);
  const { tables, loading, error } = useTables(workspaceId, connectionId, Boolean(connectionId));

  const bySchema = groupBySchema(tables);
  const schemas = [...bySchema.keys()].sort();
  const showSchema = schemas.length > 1 || (schemas.length === 1 && schemas[0] !== "");
  const parsed = parseTableRef(value);
  const [schema, setSchema] = useState(parsed.schema);
  const effectiveSchema = showSchema ? schema : (schemas[0] ?? "");
  const tablesForSchema = bySchema.get(effectiveSchema) ?? [];
  const selectedTable =
    !showSchema || parsed.schema === effectiveSchema ? parsed.table : "";

  function pickTable(tbl: string): void {
    if (!tbl) {
      onChange(undefined);
      return;
    }
    onChange(effectiveSchema ? `${effectiveSchema}.${tbl}` : tbl);
  }

  const hasPickable = tablesForSchema.length > 0 || showSchema;

  return (
    <div className="flex flex-col gap-2">
      {connectionId && !loading && !error && hasPickable ? (
        <div className="flex flex-col gap-1.5">
          {showSchema ? (
            <select
              value={schema}
              onChange={(e) => setSchema(e.target.value)}
              className={SELECT_CLS}
            >
              <option value="">{t("builder.sourceSchemaPick")}</option>
              {schemas.map((s) => (
                <option key={s} value={s}>
                  {s || t("builder.sourceSchemaDefault")}
                </option>
              ))}
            </select>
          ) : null}
          <select
            value={selectedTable}
            onChange={(e) => pickTable(e.target.value)}
            className={SELECT_CLS}
          >
            <option value="">{t("builder.sourceTablePick")}</option>
            {tablesForSchema.map((tbl) => (
              <option key={tbl.fullId} value={tbl.name}>
                {tbl.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <Input
        list={listId}
        value={(value as string) ?? ""}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value || undefined)}
      />
      <datalist id={listId}>
        {tables.map((tbl) => (
          <option key={tbl} value={tbl} />
        ))}
      </datalist>
      {!connectionId ? (
        <span className="text-[11px] text-text-muted">{t("builder.tableSelectConnFirst")}</span>
      ) : loading ? (
        <span className="text-[11px] text-text-muted">{t("builder.tableLoading")}</span>
      ) : (
        <span className="text-[11px] text-text-muted">{t("builder.tableManualHint")}</span>
      )}
    </div>
  );
}
