"use client";

import { useEffect, useState } from "react";
import { PlusIcon, XIcon } from "lucide-react";
import { Input } from "@/components/ui/input";
import { connectionsApi } from "@/lib/api";
import type { Messages } from "@/lib/i18n/messages";
import { Checkbox } from "@/components/ui/checkbox";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

/** One introspected column: name + connector-native type label. */
export interface ColumnMeta {
  name: string;
  type: string;
}

/**
 * Lazily introspect a table's columns (ADR-0033). Used to power the column
 * checklist so downstream transforms can "click" upstream result columns.
 * Carries the column's native type so pickers can show it.
 */
export function useColumns(
  workspaceId: string | undefined,
  connectionId: string | undefined,
  table: string | undefined,
  enabled: boolean,
): { columns: ColumnMeta[]; loading: boolean; error: string | null } {
  const [columns, setColumns] = useState<ColumnMeta[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !workspaceId || !connectionId || !table) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    connectionsApi
      .columns(workspaceId, connectionId, table)
      .then((res) => {
        if (!cancelled) setColumns(res.columns);
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
  }, [workspaceId, connectionId, table, enabled]);

  return { columns, loading, error };
}

function asArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

/**
 * Column multi-select. Stores a `string[]` (identical wire shape to the old
 * raw-JSON array field) but lets the user tick introspected columns instead of
 * typing JSON. Columns the introspection can't reach (or older saved values)
 * still show as removable chips, and a free-text row adds anything by hand.
 */
export function ColumnsField({
  value,
  workspaceId,
  connectionId,
  table,
  onChange,
  t,
}: {
  value: unknown;
  workspaceId?: string;
  connectionId?: string;
  table?: string;
  onChange: (v: unknown) => void;
  t: Translate;
}) {
  const selected = asArray(value);
  const enabled = Boolean(connectionId && table);
  const { columns, loading, error } = useColumns(workspaceId, connectionId, table, enabled);
  const [manual, setManual] = useState("");

  function commit(next: string[]) {
    onChange(next.length ? next : undefined);
  }
  function toggle(col: string) {
    commit(selected.includes(col) ? selected.filter((c) => c !== col) : [...selected, col]);
  }
  function addManual() {
    const c = manual.trim();
    if (c && !selected.includes(c)) commit([...selected, c]);
    setManual("");
  }

  // Columns chosen but not in the introspected set (manual / stale) — shown so
  // nothing the user picked silently disappears.
  const extra = selected.filter((c) => !columns.some((cc) => cc.name === c));

  return (
    <div className="flex flex-col gap-2">
      {!enabled ? (
        <span className="text-[11px] text-text-muted">{t("builder.columnsNeedSource")}</span>
      ) : loading ? (
        <span className="text-[11px] text-text-muted">{t("builder.columnsLoading")}</span>
      ) : error ? (
        <span className="text-[11px] text-warning">{t("builder.columnsLoadError")}</span>
      ) : columns.length === 0 ? (
        <span className="text-[11px] text-text-muted">{t("builder.columnsNone")}</span>
      ) : (
        <div className="flex flex-col gap-1 rounded-md border border-border-subtle bg-elevated p-1.5">
          {columns.map((col) => (
            <label
              key={col.name}
              className="flex cursor-pointer items-center gap-2 rounded-sm px-1.5 py-1 text-xs text-text-secondary transition duration-150 hover:bg-overlay"
            >
              <Checkbox

                checked={selected.includes(col.name)}
                onChange={() => toggle(col.name)}
              />
              <span className="font-mono">{col.name}</span>
              {col.type ? (
                <span className="ml-auto font-mono text-[10px] text-text-muted">{col.type}</span>
              ) : null}
            </label>
          ))}
        </div>
      )}

      {extra.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {extra.map((col) => (
            <span
              key={col}
              className="inline-flex items-center gap-1 rounded-sm border border-border-subtle px-1.5 py-0.5 font-mono text-[11px] text-text-secondary"
            >
              {col}
              <button
                type="button"
                aria-label={t("builder.removeRow")}
                onClick={() => commit(selected.filter((c) => c !== col))}
                className="text-text-muted transition duration-150 hover:text-error"
              >
                <XIcon size={11} />
              </button>
            </span>
          ))}
        </div>
      ) : null}

      <div className="flex items-center gap-1.5">
        <Input
          value={manual}
          placeholder={t("builder.columnsAddPlaceholder")}
          className="min-w-0 flex-1"
          onChange={(e) => setManual(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addManual();
            }
          }}
        />
        <button
          type="button"
          onClick={addManual}
          disabled={!manual.trim()}
          className="inline-flex h-7 items-center gap-1 rounded-sm border border-border-subtle px-2 text-xs text-text-secondary transition duration-150 hover:border-border-strong hover:bg-overlay hover:text-text disabled:opacity-40"
        >
          <PlusIcon size={13} />
          {t("builder.columnsAdd")}
        </button>
      </div>
    </div>
  );
}
