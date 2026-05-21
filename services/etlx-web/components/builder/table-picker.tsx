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

/** Table/collection name input backed by an introspected <datalist>. Free
 *  text always works; suggestions appear once a connection is selected. */
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
  const [focused, setFocused] = useState(false);
  const [listId] = useState(() => `${TABLE_DATALIST_PREFIX}${(_datalistSeq += 1)}`);
  const { tables, loading, error } = useTables(workspaceId, connectionId, focused);

  return (
    <div className="flex flex-col gap-1">
      <Input
        list={listId}
        value={(value as string) ?? ""}
        placeholder={placeholder}
        onFocus={() => setFocused(true)}
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
      ) : error ? (
        <span className="text-[11px] text-warning">{t("builder.tableLoadError")}</span>
      ) : null}
    </div>
  );
}
